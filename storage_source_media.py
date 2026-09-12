"""Existing Run Manager/source-track archives on the accepted storage index."""
import copy
from pathlib import Path
import uuid

if __package__:
    from . import storage_state as state
    from .storage_project import payload_key, payload_catalog
    from .storage_layout import OrganizedStorageLayout
else:
    import storage_state as state
    from storage_project import payload_key, payload_catalog
    from storage_layout import OrganizedStorageLayout


class SourceArchive:
    def __init__(self, runtime):
        self.runtime, self.base = runtime, runtime.accepted
        self.staged, self.changes, self.replacements = {}, {}, {}
        self.scopes = {'branch:'+runtime.selected}
        self.document = None

    def media(self, source, logical):
        runtime = self.runtime
        runtime.branches._require_write()
        path = Path(source)
        digest = state._hash(path.read_bytes()) if path.stat().st_size < 1024*1024 else None
        if digest is None:
            import hashlib
            with path.open('rb') as handle:
                digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        key = payload_key(logical)
        if key in self.base.state['documents']:
            descriptor = self.base.state['documents'][key]
            record = state._decode(self.base.read(key))
            target = runtime.store.payload_path(self.base, logical, verify=True)
            if record['file']['sha256'] != digest:
                raise state.StateConflict('Source archive address contains different media.')
            self.scopes.add(descriptor['scope'])
            return target, digest
        if logical not in self.staged:
            identity = uuid.uuid5(uuid.NAMESPACE_URL, 'h3-source:'+logical+':'+digest+':'+str(path)).hex
            suffix = path.suffix if len(path.suffix) <= 12 else '.bin'
            target = OrganizedStorageLayout(str(runtime.project)).project_data('assets', 'sources', identity+suffix)
            self.staged[logical] = runtime.store.stage_payload(logical, path, target,
                scope='project', operation_id=identity)
        return runtime.project/self.staged[logical]['record']['file']['path'], digest

    def control(self, logical, value):
        prefix = '' if self.runtime.selected == 'main' else 'branches/'+self.runtime.selected+'/'
        if logical not in ('references/manifest.json', prefix+'source_timeline.json'):
            raise ValueError('Unsupported source archive control.')
        previous = self.base.state['documents'].get(logical)
        contract = ({key:previous[key] for key in ('scope','category','immutable')} if previous else
            dict(scope='project' if logical.startswith('references/') else 'branch:'+self.runtime.selected,
                 category='assets', immutable=False))
        if previous:
            self.base.read(logical)
            self.scopes.add(previous['scope'])
            if previous['immutable']:
                self.replacements[logical] = previous
        self.changes[logical] = dict(data=state._encode(value), **contract)

    def commit(self):
        with self.runtime.branches._commit_guard():
            self._commit()

    def _commit(self):
        runtime = self.runtime
        for logical, receipt in self.staged.items():
            record = runtime.store._staged_record(receipt)
            self.changes[payload_key(logical)] = dict(data=state._encode(record),
                scope=record['scope'], category='payloads', immutable=True)
        if self.changes:
            receipt = runtime.store._commit_changes(self.base, self.changes,
                operation_id=uuid.uuid4().hex, read_scopes=self.scopes,
                replace_documents=self.replacements)
            runtime.record_commit(receipt)


def save_assets(runtime, store, run, bindings, policies):
    transaction = SourceArchive(runtime)
    cloned = copy.copy(store)
    cloned._organized_archive = transaction
    with runtime.branches._commit_guard():
        result = cloned.save(run, bindings, policies)
        transaction.commit()
    return result
