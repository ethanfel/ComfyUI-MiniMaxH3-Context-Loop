"""Resume accepted deferred cleanup in a recovered ordinary project.

The approval and archived Plan are never republished. Only explicitly rejected,
unshared inactive takes can move into a reversible, hash-bound quarantine.
Incomplete moves roll back before dependency validation and an exact retry.
"""
import copy
import hashlib
import os
from pathlib import Path
import re
import shutil
import uuid

from . import storage_state as state
from .storage_resolver import confined, storage_state
from .storage_deferred_review import address, validate_identity
from .storage_deferred_finalization import decision_address, project_decision, check_choice, COMPLETION
from .storage_retention import _PROCESSING, _PROCESSING_FORMATS, _mentions_source, FORMAT as RETIREMENT
from .storage_quarantine import RECEIPT, STATUS
from .checkpoint_manager import CheckpointGraphManager, checkpoint_run_lock
from .checkpoint_manager import _ARTIFACT_KEYS, _ARCHIVE_KEYS
from .artifact_paths import artifact_address
from .project_ownership import project_write_guard
from .branch_scope import current_branch
from .processing_persistence import publish_new_file, sync_directory
from .storage_layout import OrganizedStorageLayout

FORMAT = 'h3_recovered_review_quarantine_v1'


def _hash(path):
    with path.open('rb') as handle:
        before = os.fstat(handle.fileno())
        hasher = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024*1024), b''):
            hasher.update(chunk)
        digest = hasher.hexdigest()
        after = os.fstat(handle.fileno())
    latest = path.stat()
    fields = ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
    if any(getattr(before,k) != getattr(after,k) or getattr(after,k) != getattr(latest,k) for k in fields):
        raise state.StateConflict('Recovered cleanup file changed during verification.')
    return dict(sha256=digest, size=after.st_size)


class RecoveredReview:
    def __init__(self, chain, run, token):
        self.chain, self.run, self.token = chain, chain._strict_run_name(run), state._token(token)
        self.branch = current_branch(self.run)
        self.output = Path(chain._output_root()).resolve()
        self.root = self.output/'h3_chains'/self.run
        if storage_state(self.root) is not None:
            raise state.StateConflict('Recovered review requires an ungated ordinary project.')
        self.budget = 240 if os.name == 'nt' else 4096
        self.after_stage = None
        self.document = validate_identity(self.read(address(self.branch,self.token)), self.run,self.branch,self.token)
        self.decision = self.read(decision_address(self.branch,self.token))
        self.completed_key = decision_address(self.branch,self.token,complete=True)
        self.completed = self.optional(self.completed_key)
        self.projected = project_decision(self.document,self.decision,self.completed)
        self.request = self.decision['request']
        self._accepted_controls((address(self.branch,self.token),decision_address(self.branch,self.token)))
        self.candidates = {state._token(c['segment']['revision']):c['segment'] for c in self.document['candidates']}
        if len(self.candidates) != len(self.document['candidates']):
            raise ValueError('Recovered pending batch repeats a candidate.')
        self.scene = self.document['scene']

    def read(self, key):
        return state._decode(state._read_bytes(confined(self.root,key)))

    def optional(self, key):
        try:
            return self.read(key)
        except FileNotFoundError:
            return None

    def publish(self, key, value):
        state._immutable(self.root,key,state._encode(value),self.budget)

    def _accepted_controls(self, keys):
        """Check recovered immutable decisions against the preserved accepted head.

        This reads the archived commit log; it never opens a runtime, activates
        an old root or acknowledges/writes historical coordination files.
        """
        from .storage_recovery import RECOVERY
        from .storage_commit_log import CommitLog
        archive = confined(self.output,RECOVERY+'/authority')
        raw = state._read_bytes(confined(archive,'storage.json'))
        marker = state._decode(raw)
        if 'commit_protocol' in marker:
            marker = CommitLog(archive,raw).read().value
        root = state._decode(state._reference(archive,marker['root'],r'project/roots/[0-9a-f]{32}\.json'))
        if (marker.get('phase') != 'ready' or marker.get('run_name') != self.run
                or root.get('format') != state.ROOT or root.get('run_name') != self.run
                or marker.get('generation') != root.get('generation')
                or marker.get('epoch') != root.get('epoch')):
            raise state.StateConflict('Recovered review has no valid accepted storage head.')
        for key in keys:
            reference = root['documents'].get(key,{}).get('file')
            if not reference or _hash(confined(self.root,key)) != {k:reference[k] for k in ('sha256','size')}:
                raise state.StateConflict('Recovered immutable control differs from accepted storage: '+key)
        return archive

    def stage(self, name):
        if self.after_stage is not None:
            self.after_stage(name)

    def verify_selection(self):
        controls = self.decision.get('controls_sha256')
        prefix = '' if self.branch == 'main' else 'branches/'+self.branch+'/'
        if not isinstance(controls,dict) or prefix+'plan.json' not in controls:
            raise state.StateConflict('Accepted review has no exact recovery Plan witness.')
        for key,digest in controls.items():
            if key not in {prefix+name+'.json' for name in ('plan','workflow','api_prompt')}:
                raise state.StateConflict('Recovered review archive belongs to another branch.')
            if _hash(confined(self.root,key))['sha256'] != digest:
                raise state.StateConflict('Recovered branch settings changed after approval; no cleanup was performed.')
        lineage = self.chain._review_candidate_resume_revisions(self.run,self.scene,self.request['revision'])
        for item in lineage:
            active = self.read(prefix+'checkpoints/clip_%04d.json' % item['scene'])
            metadata,_ = self.chain._load_checkpoint_revision(self.run,item['scene'],item['revision'])
            if active.get('segment') != metadata.get('segment'):
                raise state.StateConflict('Recovered selected lineage changed after approval; no cleanup was performed.')
        return lineage

    def _operation(self, revision):
        return uuid.uuid5(uuid.UUID(self.token),'recovered-cleanup:'+self.branch+':'+revision).hex

    def _key(self, operation, name):
        return 'retention/'+state._token(operation)+'/recovered_'+name+'.json'

    def _validate_plan(self, plan, operation):
        if (not isinstance(plan,dict) or plan.get('format') != FORMAT
                or plan.get('run_name') != self.run or plan.get('branch_id') != self.branch
                or plan.get('token') != self.token or plan.get('request') != self.request
                or plan.get('operation_id') != operation
                or plan.get('revision') not in self.candidates
                or plan['revision'] in self.request['kept']
                or self._operation(plan['revision']) != operation
                or not isinstance(plan.get('files'),list) or not plan['files']):
            raise state.StateConflict('Recovered quarantine belongs to another accepted review.')
        seen = set()
        for item in plan['files']:
            if (not isinstance(item,dict) or set(item) != {'path','target','sha256','size'}
                    or not isinstance(item['path'],str) or not isinstance(item['target'],str)
                    or not isinstance(item['sha256'],str) or re.fullmatch('[0-9a-f]{64}',item['sha256']) is None
                    or type(item['size']) is not int or item['size'] < 0
                    or item['path'] in seen):
                raise state.StateConflict('Recovered quarantine file witness is invalid.')
            expected = 'retention/'+operation+'/files/'+state._hash(item['path'].encode())
            if item['target'] != expected or item['path'].startswith(('retention/','jobs/','pending_reviews/')):
                raise state.StateConflict('Recovered quarantine cannot move authority or unrelated staging.')
            confined(self.root,item['path']); confined(self.root,item['target'])
            seen.add(item['path'])
        metadata_key = 'checkpoints/clip_%04d.%s.json' % (self.scene,plan['revision'])
        if metadata_key not in seen:
            raise state.StateConflict('Recovered quarantine must own its rejected revision metadata.')
        witness = next(item for item in plan['files'] if item['path'] == metadata_key)
        existing = self._locations(witness)
        metadata = state._decode(state._read_bytes(existing[0]))
        allowed = self._candidate_paths(metadata,plan['revision'])
        review_pattern = r'reviews/clip_%04d\.%s\.[^/]+\.review\.mp4' % (
            self.scene,re.escape(str(self.candidates[plan['revision']].get('segment_sha256',''))[:12]))
        for path in seen:
            if path not in allowed and re.fullmatch(review_pattern,path) is None:
                raise state.StateConflict('Quarantine file does not belong to the rejected take: '+path)
        return plan

    def _candidate_paths(self, metadata, revision):
        if (not isinstance(metadata,dict) or metadata.get('run_name') != self.run
                or not isinstance(metadata.get('segment'),dict)
                or self.chain._public_segment(metadata['segment']) != self.candidates[revision]):
            raise state.StateConflict('Rejected metadata differs from the accepted review batch.')
        prefix = 'h3_chains/'+self.run+'/'
        named = 'clip_%04d.%s.' % (self.scene,revision)
        paths = {'checkpoints/clip_%04d.%s.json' % (self.scene,revision)}
        for field in _ARTIFACT_KEYS:
            value = metadata['segment'].get(field)
            if not value:
                continue
            address = artifact_address(value)
            if not address.startswith(prefix):
                raise state.StateConflict('Rejected metadata references another project.')
            key = address[len(prefix):]
            # Adopted/shared files may be referenced, but never belong to this
            # rejected take's quarantine. The graph separately protects them.
            if (key.split('/')[0] in ('segments','checkpoints','generated_audio','blend_segments')
                    and len(key.split('/')) == 2 and key.split('/')[1].startswith(named)):
                paths.add(key)
        for field,value in (metadata.get('archives') or {}).items():
            if field not in _ARCHIVE_KEYS or not value:
                continue
            expected = 'recovery_archives/'+revision+'/'+field+'.json'
            if artifact_address(value) == prefix+expected:
                paths.add(expected)
        return paths

    def _verify_files(self, plan, *, archived):
        for item in plan['files']:
            selected, other = ('target','path') if archived else ('path','target')
            if confined(self.root,item[other]).exists():
                raise state.StateConflict('Quarantine retry would overwrite another file: '+item[other])
            if _hash(confined(self.root,item[selected])) != {k:item[k] for k in ('sha256','size')}:
                raise state.StateConflict('Quarantine bytes changed: '+item[selected])

    def _locations(self, item):
        present = [confined(self.root,item[key]) for key in ('path','target')
                   if confined(self.root,item[key]).exists()]
        # The portable no-replace primitive can stop between link and unlink.
        # Only two names for the SAME file are resumable, never equal copies.
        if (not present or (len(present) == 2 and not os.path.samefile(*present))
                or any(_hash(path) != {k:item[k] for k in ('sha256','size')} for path in present)):
            raise state.StateConflict('Quarantine files are missing, changed or destinations are occupied.')
        return present

    def _move(self, plan, *, restore=False):
        source,target = ('target','path') if restore else ('path','target')
        # Validate every destination before moving any file. A conflicting
        # late member must not cause a partially restored/deleted take.
        for item in plan['files']:
            self._locations(item)
        for item in plan['files']:
            src,dst = confined(self.root,item[source]),confined(self.root,item[target])
            if dst.exists():
                if _hash(dst) != {k:item[k] for k in ('sha256','size')}:
                    raise state.StateConflict('Quarantine destination is occupied; nothing was overwritten.')
                if src.exists():
                    if not os.path.samefile(src,dst):
                        raise state.StateConflict('Quarantine destination is occupied; nothing was overwritten.')
                    src.unlink()  # Finish this exact file's interrupted link/unlink, preserving dst.
                    sync_directory(src.parent)
                continue  # This exact move completed before an interruption.
            if _hash(src) != {k:item[k] for k in ('sha256','size')}:
                raise state.StateConflict('Quarantine source bytes changed; nothing was overwritten.')
            state._mkdir(dst.parent,self.root)
            publish_new_file(src,dst)
            sync_directory(src.parent); sync_directory(dst.parent)
            self.stage('restore_file' if restore else 'quarantine_file')

    def _processing_blockers(self, preview, revision):
        # Legacy graph checks cover branches, final cuts and sealed chapters;
        # also retain generation inputs required by saved processing passes.
        paths = {item['path'] for item in preview['files']}
        blockers = []
        for path in self.root.rglob('*.json'):
            logical = path.relative_to(self.root).as_posix()
            match = _PROCESSING.fullmatch(logical)
            if not match or not (re.fullmatch(r'checkpoints/clip_\d{4}(?:\.[0-9a-f]{32})?\.json',match[1])
                                 or match[1].endswith('manifest.json')):
                continue
            value = self.read(logical)
            if (not isinstance(value,dict) or value.get('format') not in _PROCESSING_FORMATS
                    or value.get('run_name') != self.run
                    or (value['format'] == 'h3_chain_upscale_segment_v1'
                        and not isinstance(value.get('segment'),dict))
                    or (value['format'] != 'h3_chain_upscale_segment_v1'
                        and not isinstance(value.get('segments'),list))):
                raise state.StateConflict('Cannot verify recovered processing dependencies: '+logical)
            if _mentions_source(value,self.scene,revision,paths):
                blockers.append('Saved processing output requires this rejected take: '+logical)
        return blockers

    def _prior_quarantine(self, revision):
        # Already completed organized-store steps remain accepted after recovery.
        # Missing candidates without a matching accepted quarantine are errors.
        matches = []
        directory = self.root/'retention'
        for path in directory.glob('*/receipt.json') if directory.exists() else ():
            operation = state._token(path.parent.name)
            record = self.read(path.relative_to(self.root).as_posix())
            if record.get('format') != RECEIPT or record.get('action') != 'quarantine':
                continue
            try:
                reason = state._decode(record['preview']['reason'].encode())
            except (ValueError,KeyError,AttributeError):
                continue  # The generic quarantine primitive also permits text reasons.
            if reason != dict(format=RETIREMENT,branch_id=self.branch,scene=self.scene,revision=revision):
                continue
            status = self.read('retention/'+operation+'/state.json')
            if status != dict(format=STATUS,status='quarantined',operation_id=operation):
                continue
            archive = self._accepted_controls(('retention/'+operation+'/receipt.json',
                                               'retention/'+operation+'/state.json'))
            base = state._decode(state._reference(archive,record['preview']['base'],r'project/roots/[0-9a-f]{32}\.json'))
            metadata_key = 'checkpoints/clip_%04d.%s.json' % (self.scene,revision)
            items = record['preview']['items']
            if (record.get('operation_id') != operation or record['preview'].get('project') != self.run
                    or not any(item['address'] == metadata_key for item in items)):
                raise state.StateConflict('Recovered quarantine does not identify its rejected metadata.')
            from .storage_recovery import RECOVERY
            for item in items:
                if base['documents'].get(item['key']) != item['descriptor']:
                    raise state.StateConflict('Recovered quarantine differs from its accepted source descriptors.')
                descriptor = item['descriptor']['file']
                source = confined(self.output,RECOVERY+'/authority/'+descriptor['path'])
                if _hash(source) != {k:descriptor[k] for k in ('sha256','size')}:
                    raise state.StateConflict('Previously quarantined recovery evidence changed.')
                if item['address'] == metadata_key:
                    self._candidate_paths(state._decode(state._read_bytes(source)),revision)
                if 'payload' in item:
                    if state._decode(state._read_bytes(source)) != item['payload']:
                        raise state.StateConflict('Recovered quarantine payload witness changed.')
                    payload = item['payload']['file']
                    source = confined(self.output,RECOVERY+'/authority/'+payload['path'])
                    if _hash(source) != {k:payload[k] for k in ('sha256','size')}:
                        raise state.StateConflict('Previously quarantined recovery payload changed.')
            matches.append(operation)
        if len(matches) != 1:
            raise state.StateConflict('Rejected candidate is missing without one exact accepted quarantine.')
        return dict(revision=revision,outcome='quarantined',operation_id=matches[0],origin='organized')

    def _cleanup_one(self, revision):
        # Undo of a quarantine accepted before recovery is also an accepted
        # choice. Finishing the older batch must not quarantine it again.
        for path in (self.root/'retention').glob('*/recovered_organized_restored.json'):
            marker = self.read(path.relative_to(self.root).as_posix())
            if marker.get('token') == self.token and marker.get('revision') == revision:
                undo = RecoveredOrganizedUndo(self,path.parent.name,revision)
                if not undo.preview_undo(path.parent.name)['restored']:
                    raise state.StateConflict('Previously restored review take has no exact undo witness.')
                return dict(revision=revision,outcome='quarantined',operation_id=path.parent.name,
                            origin='organized',restored=True)
        operation = self._operation(revision)
        key = self._key(operation,'plan')
        plan = self.optional(key)
        completed = self.optional(self._key(operation,'complete'))
        if plan is not None:
            self._validate_plan(plan,operation)
            if completed is not None:
                if completed != dict(format=FORMAT,plan_sha256=state._hash(state._encode(plan))):
                    raise state.StateConflict('Recovered quarantine completion differs from its exact plan.')
                restored = self.optional(self._key(operation,'restored'))
                if restored is not None and restored != completed:
                    raise state.StateConflict('Recovered undo differs from its exact quarantine plan.')
                self._verify_files(plan,archived=restored is None)
                return dict(revision=revision,outcome='quarantined',operation_id=operation,origin='recovered')
            # A crash may leave some files staged. Restore only their exact
            # bytes before scanning all current dependencies again.
            self._move(plan,restore=True)
        elif completed is not None:
            raise state.StateConflict('Recovered quarantine completion has no file plan.')
        self.verify_selection()
        manager = CheckpointGraphManager(self.output)
        try:
            preview = manager.deletion_preview(self.run,self.scene,revision)
        except FileNotFoundError:
            return self._prior_quarantine(revision)
        blockers = list(preview['blockers'])+self._processing_blockers(preview,revision)
        if preview['active'] or preview['rollback']:
            blockers.append('Recovered cleanup never changes a selected checkpoint assignment.')
        if blockers:
            return dict(revision=revision,outcome='blocked',blockers=blockers)
        metadata,_ = self.chain._load_checkpoint_revision(self.run,self.scene,revision)
        if self.chain._public_segment(metadata.get('segment',{})) != self.candidates[revision]:
            raise state.StateConflict('Rejected candidate differs from the accepted review batch.')
        files = []
        for item in preview['files']:
            if not item['owned']:
                continue
            prefix = 'h3_chains/'+self.run+'/'
            if not item['path'].startswith(prefix) or not item['exists']:
                raise state.StateConflict('Owned rejected artifact is missing or outside its project.')
            logical = item['path'][len(prefix):]
            target = 'retention/'+operation+'/files/'+state._hash(logical.encode())
            OrganizedStorageLayout(str(self.root),self.budget).check_budget(target)
            files.append(dict(path=logical,target=target,**_hash(confined(self.root,logical))))
        prepared = dict(format=FORMAT,run_name=self.run,branch_id=self.branch,token=self.token,
            request=self.request,operation_id=operation,revision=revision,files=files)
        self._validate_plan(prepared,operation)
        if plan is not None and plan != prepared:
            raise state.StateConflict('Rejected artifact ownership changed after cleanup preparation.')
        self.publish(key,prepared)
        self.stage('prepared')
        self._move(prepared)
        self._verify_files(prepared,archived=True)
        self.publish(self._key(operation,'complete'),dict(format=FORMAT,plan_sha256=state._hash(state._encode(prepared))))
        self.stage('committed')
        return dict(revision=revision,outcome='quarantined',operation_id=operation,origin='recovered')

    def run_action(self, action, revision, kept, proof):
        if action not in ('prepare','finalize'):
            raise ValueError('Unknown recovered review action.')
        check_choice(self.projected,revision,kept)
        with checkpoint_run_lock(self.output,self.run), project_write_guard(
                self.output,self.run,proof,'resume accepted review cleanup'):
            if current_branch(self.run) != self.branch:
                raise state.StateConflict('Recovered review branch changed.')
            # Freshly read immutable decision bytes under the mutation locks.
            if self.read(decision_address(self.branch,self.token)) != self.decision:
                raise state.StateConflict('Recovered approval changed before cleanup.')
            self.completed = self.optional(self.completed_key)
            project_decision(self.document,self.decision,self.completed)
            lineage = self.verify_selection()
            if self.completed is None:
                if action == 'prepare':
                    return dict(self.decision['response'],action=action,activation_required=False,resume_revisions=lineage)
                steps = [self._cleanup_one(r) for r in self.candidates if r not in self.request['kept']]
                self.completed = dict(format=COMPLETION,request=self.request,origin='recovered',steps=steps)
                self.publish(self.completed_key,self.completed)
                self.stage('finalized')
            steps = self.completed['steps']
            quarantined = [s for s in steps if s['outcome'] == 'quarantined']
            return dict(self.decision['response'],action=action,activation_required=False,
                deleted_candidate_count=0,quarantined_candidate_count=len(quarantined),reclaimed_bytes=0,
                cleanup_warnings=[' '.join(s['blockers']) for s in steps if s['outcome'] == 'blocked'],
                undo_operations=[s.get('operation_id') or s['receipt']['operation_id'] for s in quarantined],
                finalization_complete=True)

    @classmethod
    def for_operation(cls, chain, run, operation):
        operation = state._token(operation)
        root = Path(chain._output_root()).resolve()/'h3_chains'/chain._strict_run_name(run)
        key = 'retention/'+operation+'/recovered_plan.json'
        try:
            plan = state._decode(state._read_bytes(confined(root,key)))
        except FileNotFoundError:
            return RecoveredOrganizedUndo.for_operation(chain,run,operation)
        service = cls(chain,run,plan['token'])
        service._validate_plan(plan,operation)
        return service

    def preview_undo(self, operation):
        operation = state._token(operation)
        plan = self._validate_plan(self.read(self._key(operation,'plan')),operation)
        fingerprint = dict(format=FORMAT,plan_sha256=state._hash(state._encode(plan)))
        if self.optional(self._key(operation,'complete')) != fingerprint:
            raise state.StateConflict('Finish interrupted cleanup before requesting its undo.')
        restored = self.optional(self._key(operation,'restored'))
        if restored is not None and restored != fingerprint:
            raise state.StateConflict('Recovered undo witness changed.')
        # An exact retry may have restored some files before the reply failed.
        for item in plan['files']:
            self._locations(item)
        if restored is not None:
            self._verify_files(plan,archived=False)
        return dict(operation_id=operation,run_name=self.run,branch_id=self.branch,
            snapshot=state._hash(state._encode(fingerprint)),allowed=True,
            restored=restored is not None,files=copy.deepcopy(plan['files']),reclaimed_bytes=0)

    def undo(self, operation, snapshot, *, proof):
        with checkpoint_run_lock(self.output,self.run), project_write_guard(
                self.output,self.run,proof,'restore a rejected review take'):
            preview = self.preview_undo(operation)
            if snapshot != preview['snapshot']:
                raise state.StateConflict('Confirm this exact recovered quarantine undo preview.')
            if not preview['restored']:
                plan = self.read(self._key(operation,'plan'))
                self._move(plan,restore=True)
                self._verify_files(plan,archived=False)
                self.publish(self._key(operation,'restored'),self.read(self._key(operation,'complete')))
                self.stage('restored')
            return dict(preview,ok=True,restored=True)


class RecoveredOrganizedUndo:
    """Restore a deferred-review quarantine from preserved recovery evidence.

    The old immutable storage slots are copied, never consumed or rewritten.
    This is deliberately scoped to an accepted inactive review rejection, not
    generic rollback of assignments, chapter retirement or arbitrary receipts.
    """
    FORMAT = 'h3_recovered_organized_review_undo_v1'

    def __init__(self, review, operation, revision):
        self.review,self.operation,self.revision = review,state._token(operation),state._token(revision)
        self.key = 'retention/'+self.operation+'/recovered_organized_restored.json'

    @classmethod
    def for_operation(cls, chain, run, operation):
        root = Path(chain._output_root()).resolve()/'h3_chains'/chain._strict_run_name(run)
        record = state._decode(state._read_bytes(confined(root,'retention/'+operation+'/receipt.json')))
        reason = state._decode(record['preview']['reason'].encode())
        if (reason.get('format') != RETIREMENT or reason.get('branch_id') != current_branch(run)
                or record.get('format') != RECEIPT or record.get('action') != 'quarantine'):
            raise state.StateConflict('This receipt is not an accepted rejection on the selected branch.')
        directory = confined(root,address(current_branch(run),'0'*32)).parent
        matches = []
        for path in directory.glob('*.json'):
            if re.fullmatch('[0-9a-f]{32}',path.stem) is None:
                continue
            decision = decision_address(current_branch(run),path.stem)
            if not confined(root,decision).exists():
                continue
            review = RecoveredReview(chain,run,path.stem)
            if (reason.get('scene') == review.scene and reason.get('revision') in review.candidates
                    and reason['revision'] not in review.request['kept']):
                matches.append(review)
        if len(matches) != 1:
            raise state.StateConflict('Recovered undo needs one exact accepted review rejection.')
        return cls(matches[0],operation,reason['revision'])

    def preview_undo(self, operation):
        review = self.review
        if state._token(operation) != self.operation:
            raise state.StateConflict('Recovered undo operation changed.')
        step = review._prior_quarantine(self.revision)
        if step['operation_id'] != self.operation:
            raise state.StateConflict('Recovered undo does not match the accepted quarantine.')
        record = review.read('retention/'+self.operation+'/receipt.json')
        preview = record['preview']
        if (preview.get('updates') or preview.get('archives')
                or preview.get('sha256') != state._hash(state._encode({k:v for k,v in preview.items() if k != 'sha256'}))):
            raise state.StateConflict('Recovered review undo cannot reinterpret control changes or a changed receipt.')
        from .storage_recovery import RECOVERY
        metadata_key = 'checkpoints/clip_%04d.%s.json' % (review.scene,self.revision)
        item = next(item for item in preview['items'] if item['address'] == metadata_key)
        metadata = state._decode(state._read_bytes(confined(review.output,
            RECOVERY+'/authority/'+item['descriptor']['file']['path'])))
        allowed = review._candidate_paths(metadata,self.revision)
        pattern = r'reviews/clip_%04d\.%s\.[^/]+\.review\.mp4' % (
            review.scene,re.escape(str(review.candidates[self.revision].get('segment_sha256',''))[:12]))
        files = []
        for item in preview['items']:
            path = item['address']
            if path not in allowed and re.fullmatch(pattern,path) is None:
                raise state.StateConflict('Recovered undo receipt includes a file outside this rejected take.')
            reference = item['payload']['file'] if 'payload' in item else item['descriptor']['file']
            source = RECOVERY+'/authority/'+reference['path']
            actual = {k:reference[k] for k in ('sha256','size')}
            if _hash(confined(review.output,source)) != actual:
                raise state.StateConflict('Recovered undo source changed.')
            destination = confined(review.root,path)
            if destination.exists() and _hash(destination) != actual:
                raise state.StateConflict('Recovered undo destination is occupied; nothing was overwritten.')
            files.append(dict(path=path,source=source,**actual))
        fingerprint = state._hash(state._encode(dict(record=record,files=files)))
        marker = dict(format=self.FORMAT,operation_id=self.operation,token=review.token,
            branch_id=review.branch,revision=self.revision,snapshot=fingerprint)
        restored = review.optional(self.key)
        if restored is not None:
            if restored != marker or any(not confined(review.root,item['path']).is_file() for item in files):
                raise state.StateConflict('Recovered undo completion differs from the restored files.')
        return dict(operation_id=self.operation,run_name=review.run,branch_id=review.branch,
                    snapshot=fingerprint,allowed=True,restored=restored is not None,files=files,reclaimed_bytes=0)

    def undo(self, operation, snapshot, *, proof):
        review = self.review
        with checkpoint_run_lock(review.output,review.run), project_write_guard(
                review.output,review.run,proof,'restore a recovered review quarantine'):
            preview = self.preview_undo(operation)
            if snapshot != preview['snapshot']:
                raise state.StateConflict('Confirm this exact recovered undo preview.')
            if not preview['restored']:
                for item in preview['files']:
                    destination = confined(review.root,item['path'])
                    if destination.exists():
                        continue  # Fully verified exact member of a prior partial undo.
                    state._mkdir(destination.parent,review.root)
                    staging = destination.parent/('.tmp-'+uuid.uuid4().hex)
                    # Interrupted staging is retained as evidence, never reused
                    # as a source or accepted merely because its name exists.
                    with confined(review.output,item['source']).open('rb') as source, staging.open('xb') as target:
                        shutil.copyfileobj(source,target,1024*1024)
                        target.flush(); os.fsync(target.fileno())
                    if _hash(staging) != {k:item[k] for k in ('sha256','size')}:
                        raise state.StateConflict('Recovered undo source changed while copying.')
                    publish_new_file(staging,destination)
                    sync_directory(destination.parent)
                    review.stage('organized_restore_file')
                self.preview_undo(operation)
                review.publish(self.key,dict(format=self.FORMAT,operation_id=self.operation,token=review.token,
                    branch_id=review.branch,revision=self.revision,snapshot=preview['snapshot']))
                review.stage('organized_restored')
            return dict(preview,ok=True,restored=True)
