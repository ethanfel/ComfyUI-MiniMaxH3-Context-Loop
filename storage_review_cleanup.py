"""Post-acceptance candidate quarantine with durable, exact retry boundaries.

Review publishes its choice first. Each deletion then uses the typed retention
service and its own undo receipt. Prepared previews and a final cleanup witness
prevent a retry from deleting a newly referenced take or re-deleting an undone
take. No physical media is removed; the original Save remains independently
verifiable through its old accepted pin.
"""
import copy
import uuid

if __package__:
    from . import storage_state as control
    from .storage_quarantine import _address as retention_address, RECEIPT
    from .storage_retention import FORMAT as RETENTION_FORMAT
    from .storage_resolver import confined
else:
    import storage_state as control
    from storage_quarantine import _address as retention_address, RECEIPT
    from storage_retention import FORMAT as RETENTION_FORMAT
    from storage_resolver import confined

FORMAT = 'h3_review_cleanup_v1'
KEY = '_storage_review_cleanup'


def _operation(review_receipt):
    return uuid.uuid5(uuid.UUID(control._token(review_receipt['operation_id'])), 'candidate-cleanup').hex


def _address(operation):
    return 'jobs/'+control._token(operation)+'/review-cleanup.json'


def _request(review_receipt, prune):
    return dict(format=FORMAT, review_receipt=review_receipt, prune=prune)


def verify_cleanup(store, proof, review_receipt):
    """Verify the exact Review -> quarantine(s) -> cleanup successor chain."""
    operation = _operation(review_receipt)
    if (not isinstance(proof, dict) or set(proof) != {'receipt', 'witness'}
            or proof['witness'] != _address(operation)
            or proof['receipt'].get('operation_id') != operation):
        raise ValueError('Invalid Review cleanup successor proof.')
    accepted = store.committed_snapshot(proof['receipt'])
    saved = control._decode(accepted.read(proof['witness']))
    source = store.committed_snapshot(review_receipt)
    review = control._decode(source.read('jobs/'+review_receipt['operation_id']+'/review.json'))
    prune = review.get('prune')
    if not prune or saved.get('request') != _request(review_receipt, prune):
        raise control.StateConflict('Cleanup differs from the accepted Review retention choice.')
    targets = [revision for revision in prune['available'] if revision not in prune['kept']]
    steps = saved.get('steps')
    if not isinstance(steps, list) or [step.get('revision') for step in steps] != targets:
        raise control.StateConflict('Cleanup does not cover its exact rejected candidates.')
    for step in steps:
        if step.get('base') != source.reference:
            raise control.StateConflict('Cleanup skipped or replaced an accepted predecessor.')
        if step.get('outcome') == 'blocked' and isinstance(step.get('blockers'), list) and step['blockers']:
            continue
        if step.get('outcome') != 'quarantined':
            raise ValueError('Unknown Review cleanup outcome.')
        receipt = step['receipt']
        successor = store.committed_snapshot(receipt)
        record = control._decode(successor.read(retention_address(receipt['operation_id'])))
        if (record.get('format') != RECEIPT or record.get('action') != 'quarantine'
                or record['preview']['base'] != source.reference
                or control._decode(record['preview']['reason'].encode()) != dict(
                    format=RETENTION_FORMAT, branch_id=prune['branch_id'],
                    scene=prune['scene'], revision=step['revision'])):
            raise control.StateConflict('Cleanup quarantine belongs to another target or accepted root.')
        store.validate_snapshot(source, current=successor)
        source = successor
    if saved.get('base') != source.reference:
        raise control.StateConflict('Cleanup result replaced its final quarantine root.')
    store.validate_snapshot(source, current=accepted)
    return accepted, saved


def status_note(saved):
    steps = saved['steps']
    count = sum(step['outcome'] == 'quarantined' for step in steps)
    status = '; kept %d, quarantined %d candidate%s (undo available; no disk space reclaimed)' % (
        len(saved['request']['prune']['kept']), count, '' if count == 1 else 's')
    warnings = [step['revision'][:8]+': '+' '.join(step['blockers'])
                for step in steps if step['outcome'] == 'blocked']
    if warnings:
        status += '; cleanup warning: '+' | '.join(warnings)
    return status


class ReviewCleanup:
    def __init__(self, execution, saved, receipt):
        self.execution, self.runtime = execution, execution.runtime
        self.prune, self.review_receipt = saved['prune'], receipt
        self.operation = _operation(receipt)
        self.request = _request(receipt, self.prune)
        self.directory = 'project/jobs/'+self.operation

    def run(self):
        runtime = self.runtime
        with self.execution.guard(), runtime.retention.guard(self.execution.proof):
            receipt = runtime.check().state['operations'].get(self.operation)
            if receipt is not None:
                proof = dict(receipt=receipt, witness=_address(self.operation))
                _, saved = verify_cleanup(runtime.store, proof, self.review_receipt)
                with control._lock(runtime.project):
                    runtime.store._acknowledge_commit()
                runtime.record_commit(receipt)
                return proof, saved
            steps = []
            for revision in self.prune['available']:
                if revision in self.prune['kept']:
                    continue
                path = confined(runtime.project, self.directory+'/'+revision+'.json')
                if not path.exists():
                    if runtime.check().reference != runtime.accepted.reference:
                        raise control.StateConflict('Project changed before Review cleanup; no new deletion preview was adopted.')
                    preview = runtime.retention.preview_generation(self.prune['scene'], revision)
                    prepared = dict(request=self.request, base=runtime.accepted.reference, revision=revision,
                        snapshot=preview['snapshot'], allowed=preview['allowed'], blockers=preview['blockers'])
                    control._immutable(runtime.project, path.relative_to(runtime.project).as_posix(),
                        control._encode(prepared), self.execution.budget)
                prepared = control._decode(control._read_bytes(path))
                if (prepared.get('request') != self.request or prepared.get('revision') != revision
                        or prepared.get('base') != runtime.accepted.reference):
                    raise control.StateConflict('Prepared Review cleanup belongs to another request or root.')
                # Rebuild the exact saved preview, including all typed blockers.
                preview = runtime.retention.preview_generation(self.prune['scene'], revision)
                if any(prepared[key] != preview[key] for key in ('snapshot', 'allowed', 'blockers')):
                    raise control.StateConflict('Prepared Review cleanup no longer matches its saved dependencies.')
                step = dict(revision=revision, base=copy.deepcopy(runtime.accepted.reference))
                if not prepared['allowed']:
                    step.update(outcome='blocked', blockers=prepared['blockers'])
                else:
                    result = runtime.retention.delete_generation(self.prune['scene'], revision,
                        prepared['snapshot'], proof=self.execution.proof)
                    step.update(outcome='quarantined', receipt=result['receipt'])
                steps.append(step)
            saved = dict(request=self.request, steps=steps, base=runtime.accepted.reference)
            with control._lock(runtime.project):
                if runtime.check().reference != runtime.accepted.reference:
                    raise control.StateConflict('Project changed before Review cleanup completion.')
                receipt = runtime.store.commit(runtime.accepted, {_address(self.operation):dict(
                    data=control._encode(saved), scope='jobs:'+self.operation, category='jobs', immutable=True)},
                    operation_id=self.operation)
                runtime.record_commit(receipt)
            proof = dict(receipt=receipt, witness=_address(self.operation))
            verify_cleanup(runtime.store, proof, self.review_receipt)
            return proof, saved
