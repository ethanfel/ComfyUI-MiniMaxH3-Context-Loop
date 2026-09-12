"""Explicit external ownership inventory for independent V1 recovery copies.

The restored effective record stays outside the deletable run. Exact source
authority/history/staging bytes are archived separately. Source locations are
derived only from the receipted run, never arbitrary paths from a journal.
"""
import base64
from contextlib import contextmanager
from pathlib import Path

if __package__:
    from . import storage_state as state, storage_resolver as resolver
    from .storage_ownership import authority_directory, read_authority
    from .project_ownership import _lock_for, _normalize_record
else:
    import storage_state as state
    import storage_resolver as resolver
    from storage_ownership import authority_directory, read_authority
    from project_ownership import _lock_for, _normalize_record

FORMAT = 'h3_external_ownership_recovery_v1'
ARCHIVE = '.h3-storage-recovery/ownership'


def _legacy(source):
    return resolver.confined(source.parent, '.project_ownership/'+source.name+'.json')


@contextmanager
def source_guard(source):
    # Caller already holds the shared run lock. Legacy ownership changes use
    # their own lock; keep both across capture/copy/verification/publication.
    with _lock_for(str(_legacy(source))):
        yield


def _source_files(source):
    if __package__:
        from .storage_recovery import _files
    else:
        from storage_recovery import _files
    files = {}
    legacy = _legacy(source)
    if legacy.exists():
        if not legacy.is_file():
            raise ValueError('External ownership record is not a regular file.')
        files['legacy.json'] = legacy
    directory = authority_directory(source.parent.parent, source.name)
    if directory.exists():
        # Never interpret a damaged/unfinished authority directory as unowned.
        if not resolver.confined(directory, 'storage.json').is_file():
            raise ValueError('Missing external ownership bootstrap; no recovery fallback.')
        files.update({'log/'+key: path for key, path in _files(directory).items()})
    return files


def capture(source):
    """Read-only stable snapshot. Must be called under source_guard + run lock."""
    files = _source_files(source)
    signatures, rows, raw_legacy = {}, [], None
    for key, path in sorted(files.items()):
        raw = state._read_bytes(path)
        signatures[key] = resolver._signature(path)
        rows.append({'source': key, 'sha256': state._hash(raw), 'size': len(raw)})
        if key == 'legacy.json':
            raw_legacy = raw
    if raw_legacy is not None:
        _normalize_record(state._decode(raw_legacy), source.name)
    witness = None if raw_legacy is None else {'sha256': state._hash(raw_legacy), 'size': len(raw_legacy)}
    raw_active = raw_legacy
    directory = authority_directory(source.parent.parent, source.name)
    if directory.exists():
        record = read_authority(directory, source.name, witness)['value']['record']
        if record is not None:
            _normalize_record(record, source.name)
        raw_active = None if record is None else state._encode(record)
    current = _source_files(source)
    if set(current) != set(files) or any(resolver._signature(current[key]) != signature
                                         for key, signature in signatures.items()):
        raise state.StateConflict('External ownership changed during recovery inventory.')
    return {'format': FORMAT, 'run_name': source.name, 'files': rows,
            'active_bytes': None if raw_active is None else base64.b64encode(raw_active).decode('ascii')}


def verify(source, plan):
    actual = capture(source)
    expected = plan.get('external_ownership')
    if expected is None:
        # Preserve old unowned journals, without letting them drop a new fence.
        if actual['files'] or actual['active_bytes'] is not None:
            raise ValueError('Recovery requires an external ownership inventory; no unprotected copy was published.')
    elif expected != actual:
        raise state.StateConflict('External ownership changed since recovery preparation; no publication.')
    return actual


def entries(plan):
    """Derived destination witnesses; no writable location comes from a record."""
    snapshot = plan.get('external_ownership')
    if snapshot is None:
        return []
    source = Path(plan['source'])
    if (set(snapshot) != {'format', 'run_name', 'files', 'active_bytes'}
            or snapshot['format'] != FORMAT or snapshot['run_name'] != source.name):
        raise ValueError('Invalid external ownership recovery inventory.')
    rows = []
    names = set()
    for item in snapshot['files']:
        if set(item) != {'source', 'sha256', 'size'}:
            raise ValueError('Invalid external ownership file witness.')
        key = item['source']
        if (not isinstance(key, str) or resolver.artifact_address(key) != key
                or not (key == 'legacy.json' or key.startswith('log/'))
                or key.casefold() in names):
            raise ValueError('Invalid external ownership source identity.')
        names.add(key.casefold())
        # Same bounded exact file witness used by state, with a derived path.
        state._validate_reference({'path': key, 'sha256': item['sha256'], 'size': item['size']}, r'.+')
        rows.append(dict(item, target=ARCHIVE+'/'+key, role='ownership_archive'))
    if snapshot['active_bytes'] is not None:
        raw = base64.b64decode(snapshot['active_bytes'], validate=True)
        _normalize_record(state._decode(raw), source.name)
        rows.append({'source': 'active', 'target': 'h3_chains/.project_ownership/'+source.name+'.json',
                     'sha256': state._hash(raw), 'size': len(raw), 'role': 'ownership_control'})
    return rows


def coordination(plan):
    entries(plan)  # Validate any external inventory before deriving names.
    # The ordinary status reader takes this lock even when there is no owner
    # record. Permit only this run's lock, not arbitrary *.lock additions.
    return {'h3_chains/.project_ownership/'+Path(plan['source']).name+'.json.lock'}


def copy_files(source, target, folder, plan, *, after_copy=None, offset=0):
    if __package__:
        from .storage_recovery import _copy_row
    else:
        from storage_recovery import _copy_row
    files = _source_files(source)
    for index, row in enumerate(entries(plan), start=len(plan['rows'])+offset+1):
        if row['role'] == 'ownership_control':
            raw = base64.b64decode(plan['external_ownership']['active_bytes'], validate=True)
            _copy_row(row, source, target, folder, data=raw)
        else:
            path = files[row['source']]
            # _source_files derives the exact allowed run's paths. Never let
            # a journal's logical key select a sibling project's authority.
            _copy_row(dict(row, source=path.name), path.parent, target, folder)
        if after_copy:
            after_copy(index)
