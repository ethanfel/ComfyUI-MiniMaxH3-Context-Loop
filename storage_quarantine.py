"""Reversible catalogue quarantine on explicit, independently copied projects.

This is a transaction primitive, NOT a deletion permission check or a public
node/route. The retention service must first classify required/shared/pinned
references and verify caller ownership. Only exact enumerated identities are
accepted here; there is no directory/glob purge. Bytes stay in their immutable
slots, outside the active catalogue, so old pins and undo remain byte-exact.
Permanent purge and releasing external compatibility copies are separate work.
"""
import copy

if __package__:
    from . import storage_state as state, storage_project as project
else:
    import storage_state as state
    import storage_project as project

PREVIEW = 'h3_catalogue_quarantine_preview_v1'
PREVIEW_UPDATES = 'h3_catalogue_quarantine_preview_v2'
PREVIEW_ARCHIVES = 'h3_catalogue_quarantine_preview_v3'
RECEIPT = 'h3_catalogue_quarantine_receipt_v1'
STATUS = 'h3_catalogue_quarantine_state_v1'


def _address(operation, name='receipt'):
    return 'retention/'+state._token(operation)+'/'+name+'.json'


def _change(raw, operation, immutable=True):
    return dict(data=raw, category='recovery', scope='jobs:retention_'+operation, immutable=immutable)


class ProjectQuarantine:
    def __init__(self, store):
        if not isinstance(store, project.ProjectStore):
            raise TypeError('Quarantine requires an explicit copied ProjectStore.')
        self.store = store

    def _base(self, base):
        if not isinstance(base, state.Snapshot) or base.project != self.store.project:
            raise ValueError('Quarantine requires this project\'s exact accepted snapshot.')
        self.store.validate_snapshot(base, current=self.store.snapshot())

    def preview(self, base, addresses, *, reason, updates=None, replace_immutable=(), payload_exceptions=None,
                archive_controls=None):
        """Read-only exact inventory; this does not assert deletion is allowed."""
        self._base(base)
        if (not isinstance(addresses, (tuple, list)) or not addresses
                or any(not isinstance(value, str) for value in addresses)
                or len(set(addresses)) != len(addresses)):
            raise ValueError('Quarantine requires a nonempty explicit unique identity list.')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4096:
            raise ValueError('Quarantine requires a bounded explicit reason.')
        descriptors = base.state['documents']
        if updates is not None and not isinstance(updates, dict):
            raise ValueError('Quarantine control updates require exact address/byte pairs.')
        if (payload_exceptions is not None and not isinstance(payload_exceptions, dict)):
            raise ValueError('Payload custody requires explicit identities and conditions.')
        payload_exceptions = payload_exceptions or {}
        if set(payload_exceptions)-set(addresses):
            raise ValueError('Payload custody must refer to a retired identity.')
        if (not isinstance(replace_immutable, (tuple, list))
                or any(not isinstance(address, str) for address in replace_immutable)
                or len(set(replace_immutable)) != len(replace_immutable)
                or set(replace_immutable)-set(updates or {})):
            raise ValueError('Immutable replacement requires explicit unique updated identities.')
        items = []
        for address in sorted(addresses):
            state._logical(address)
            if address.startswith(('__storage__/', '__migration__/', 'retention/', 'jobs/', 'orchestration/')):
                raise ValueError('Internal authority/job identities cannot be quarantined by this primitive.')
            key = address if address in descriptors else project.payload_key(address)
            if key not in descriptors:
                raise FileNotFoundError('Quarantine identity is not accepted: '+address)
            raw = base.read(key)
            item = dict(address=address, key=key, descriptor=descriptors[key])
            if key != address:
                record = project._indexed_record(base, key)
                item['payload'] = record
                if address in payload_exceptions:
                    if __package__:
                        from .storage_retention_custody import observe
                    else:
                        from storage_retention_custody import observe
                    custody, _ = observe(self.store.project, record, payload_exceptions[address])
                    if custody is not None:
                        item['custody'] = custody
                else:
                    self.store.payload_path(base, address, verify=True)
            elif address in payload_exceptions:
                raise ValueError('Payload custody cannot bypass missing/corrupt control metadata.')
            items.append(item)
        value = dict(format=PREVIEW, project=self.store.project.name, base=base.reference,
                     items=items, reason=reason)
        if updates:
            if not isinstance(updates, dict):
                raise ValueError('Quarantine control updates require exact address/byte pairs.')
            replacements = []
            for address, raw in sorted(updates.items()):
                state._logical(address)
                if address in addresses or address.startswith(('__storage__/', '__migration__/', 'retention/', 'jobs/', 'orchestration/')):
                    raise ValueError('Quarantine cannot update internal authority or a retired identity.')
                previous = descriptors.get(address)
                if previous is None or (previous['immutable'] and address not in replace_immutable):
                    raise ValueError('Quarantine may update only existing mutable controls.')
                if address in replace_immutable and not previous['immutable']:
                    raise ValueError('Explicit immutable replacement requires an immutable control.')
                if not isinstance(raw, bytes) or len(raw) > 32*1024*1024:
                    raise ValueError('Quarantine control updates require bounded exact bytes.')
                base.read(address)
                item = dict(address=address, descriptor=previous, data_hex=raw.hex())
                if address in replace_immutable:
                    item['replace_immutable'] = True
                replacements.append(item)
            value.update(format=PREVIEW_UPDATES, updates=replacements)
        elif updates is not None and not isinstance(updates, dict):
            raise ValueError('Quarantine control updates require exact address/byte pairs.')
        if archive_controls is not None:
            if not isinstance(archive_controls, dict):
                raise ValueError('Control archives require exact source/destination pairs.')
            archived, targets = [], set()
            owned = {item['address']:item for item in items}
            for source, address in sorted(archive_controls.items()):
                state._logical(address)
                item = owned.get(source)
                if (item is None or 'payload' in item or not item['descriptor']['immutable']
                        or address in descriptors or address in addresses or address in (updates or {})
                        or address in targets
                        or address.startswith(('__storage__/', '__migration__/', 'retention/', 'jobs/', 'orchestration/'))):
                    raise ValueError('Archive destination must be new and belong to a retired immutable control.')
                targets.add(address)
                archived.append(dict(source=source, address=address, descriptor=item['descriptor']))
            value.update(format=PREVIEW_ARCHIVES, archives=archived)
            value.setdefault('updates', [])
        return dict(value, sha256=state._hash(state._encode(value)))

    def _preview(self, value):
        keys = {'format', 'project', 'base', 'items', 'reason', 'sha256'}
        if isinstance(value, dict) and value.get('format') in (PREVIEW_UPDATES, PREVIEW_ARCHIVES):
            keys.add('updates')
        if isinstance(value, dict) and value.get('format') == PREVIEW_ARCHIVES:
            keys.add('archives')
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError('Invalid quarantine preview.')
        base = state.Snapshot(self.store.project, value['base'])
        updates = {item['address']:bytes.fromhex(item['data_hex']) for item in value.get('updates', [])}
        actual = self.preview(base, [item['address'] for item in value['items']], reason=value['reason'], updates=updates,
            archive_controls={item['source']:item['address'] for item in value['archives']} if 'archives' in value else None,
            payload_exceptions={item['address']:[item['custody']['status']] for item in value['items'] if 'custody' in item},
            replace_immutable=[item['address'] for item in value.get('updates', []) if item.get('replace_immutable') is True])
        if value != actual:
            raise state.StateConflict('Quarantine preview differs from the accepted files or descriptors.')
        return base

    def _retry(self, operation, expected):
        current = self.store.snapshot()
        receipt = current.state['operations'].get(operation)
        if receipt is None:
            return None
        accepted = self.store.committed_snapshot(receipt)
        if accepted.read(_address(operation)) != state._encode(expected):
            raise state.StateConflict('Quarantine operation was reused with another request.')
        self.store._acknowledge_commit()
        return copy.deepcopy(receipt)

    def quarantine(self, preview, *, operation_id, after_stage=None):
        operation = state._token(operation_id)
        # Revalidate retained bytes even when recovering a lost acknowledgement.
        preview = copy.deepcopy(preview)
        base = self._preview(preview)
        record = dict(format=RECEIPT, action='quarantine', operation_id=operation, preview=preview,
                      physical_policy='retain_immutable_slots', reclaimed_bytes=0)
        changes = {_address(operation):_change(state._encode(record), operation),
            _address(operation, 'state'):_change(state._encode(dict(format=STATUS, status='quarantined',
                operation_id=operation)), operation, False)}
        for item in preview.get('updates', []):
            changes[item['address']] = dict(data=bytes.fromhex(item['data_hex']),
                **{key:item['descriptor'][key] for key in ('category', 'scope', 'immutable')})
        archives = {}
        for item in preview.get('archives', []):
            archives[item['address']] = item['descriptor']
            changes[item['address']] = dict(data=base.read(item['source']),
                **{key:item['descriptor'][key] for key in ('category', 'scope', 'immutable')})
        with state._lock(self.store.project):
            receipt = self._retry(operation, record)
            if receipt is not None:
                return receipt
            if self.store.snapshot().reference != base.reference:
                raise state.StateConflict('Project changed; rebuild the typed deletion preview before quarantine.')
            # Fresh full-root comparison fences references added in any scope,
            # not just scopes of the selected files. No filesystem removal.
            return self.store._commit_changes(base, changes, operation_id=operation,
                retire_documents={item['key']:item['descriptor'] for item in preview['items']},
                replace_documents={item['address']:item['descriptor'] for item in preview.get('updates', [])
                                   if item.get('replace_immutable') is True},
                restore_documents=archives,
                after_stage=after_stage)

    def undo(self, base, quarantine_operation, *, operation_id, after_stage=None):
        self._base(base)
        operation, original = state._token(operation_id), state._token(quarantine_operation)
        if original == operation:
            raise ValueError('Undo requires its own operation identity.')
        original_record = state._decode(base.read(_address(original)))
        status = state._decode(base.read(_address(original, 'state')))
        original_commit = base.state['operations'].get(original)
        if (original_commit is None or self.store.committed_snapshot(original_commit).read(_address(original))
                != state._encode(original_record)):
            raise state.StateConflict('Quarantine record has no matching accepted transaction.')
        if (original_record.get('format') != RECEIPT or original_record.get('action') != 'quarantine'
                or original_record.get('operation_id') != original
                or status != dict(format=STATUS, status='quarantined', operation_id=original)):
            raise ValueError('Undo requires an accepted, still-quarantined receipt.')
        preview = original_record['preview']
        source = self._preview(preview)
        record = dict(format=RECEIPT, action='undo', operation_id=operation, quarantine_operation=original,
                      base=base.reference, quarantine_sha256=state._hash(state._encode(original_record)))
        changes = {_address(operation):_change(state._encode(record), operation),
            _address(original, 'state'):_change(state._encode(dict(format=STATUS, status='restored',
                operation_id=original, undo_operation=operation)), original, False)}
        descriptors = base.state['documents']
        restoring = {}
        for item in preview['items']:
            if item['key'] in descriptors:
                raise state.StateConflict('Quarantined identity is occupied; undo never overwrites later work.')
            descriptor = item['descriptor']
            changes[item['key']] = dict(data=source.read(item['key']),
                **{key:descriptor[key] for key in ('category', 'scope', 'immutable')})
            restoring[item['key']] = descriptor
        published = self.store.committed_snapshot(original_commit)
        published_descriptors = published.state['documents']
        retiring = {}
        for item in preview.get('archives', []):
            address = item['address']
            if (descriptors.get(address) != published_descriptors.get(address)
                    or base.read(address) != source.read(item['source'])):
                raise state.StateConflict('Archived control changed; undo never overwrites later work.')
            retiring[address] = descriptors[address]
        for item in preview.get('updates', []):
            address = item['address']
            if descriptors.get(address) != published_descriptors.get(address):
                raise state.StateConflict('Quarantine control changed; undo never overwrites later edits.')
            if base.read(address) != bytes.fromhex(item['data_hex']):
                raise state.StateConflict('Quarantine control differs from its accepted update.')
            restoring[address] = item['descriptor']
            changes[address] = dict(data=source.read(address),
                **{key:item['descriptor'][key] for key in ('category', 'scope', 'immutable')})
        with state._lock(self.store.project):
            receipt = self._retry(operation, record)
            if receipt is not None:
                return receipt
            if self.store.snapshot().reference != base.reference:
                raise state.StateConflict('Project changed before undo; refresh the quarantine preview.')
            # Retained payload records are accepted only through their original
            # receipt and verified bytes, never arbitrary caller control data.
            return self.store._commit_changes(base, changes, operation_id=operation, after_stage=after_stage,
                retire_documents=retiring,
                restore_documents=restoring, replace_documents={item['address']:published_descriptors[item['address']]
                    for item in preview.get('updates', []) if item.get('replace_immutable') is True})
