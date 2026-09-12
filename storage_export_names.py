"""Readable export destinations; internal claims make retries/collisions safe.

Only new exports reserve names here. Existing indexed files keep their exact
paths, including older ID-based exports and appendable PNG sequences.
"""
from pathlib import Path, PurePosixPath
import re
import uuid

if __package__:
    from . import storage_state as state
    from .storage_layout import OrganizedStorageLayout
    from .storage_resolver import confined
else:
    import storage_state as state
    from storage_layout import OrganizedStorageLayout
    from storage_resolver import confined

FORMAT = 'h3_readable_export_name_v1'


def pending_legacy(project, operation, keys, directory):
    """Keep destinations of an interrupted pre-readable-name publication."""
    for key in keys:
        identity = uuid.uuid5(uuid.UUID(operation), key).hex
        path = confined(project, 'project/jobs/payload-'+identity+'.json')
        if path.exists():
            intent = state._decode(state._read_bytes(path))
            target = intent.get('record', {}).get('file', {}).get('path', '')
            if target.startswith(directory+'/'):
                return True
    return False


def readable_name(label):
    if not isinstance(label, str) or not label.strip():
        raise ValueError('An export name is required.')
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', label).strip().rstrip('. ')
    if name in ('', '.', '..'):
        raise ValueError('An export name is required.')
    if re.fullmatch(r'CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]', name.split('.')[0], re.I):
        name = '_'+name
    return name


def reserve_base(project, kind, identity, label, *, suffixes=(), budget=240):
    """Claim a folder (PNG) or a filename family (video/audio), without overwrite.

    Callers retain their ordinary export ownership guard. Names are coordinated
    across branches/profiles, case-insensitively, under the shared project lock.
    A crash between claim and receipt is resumed by the same internal identity.
    """
    project = Path(project)
    state._token(identity)
    if kind not in ('png', 'video', 'audio'):
        raise ValueError('Unsupported export kind.')
    suffixes = tuple(sorted(set(suffixes)))
    if any(not re.fullmatch(r'\.[A-Za-z0-9.]+', item) for item in suffixes):
        raise ValueError('Invalid export filename suffix.')
    label = readable_name(label)
    policy = OrganizedStorageLayout(str(project), budget)
    request = dict(format=FORMAT, kind=kind, identity=identity, label=label, suffixes=list(suffixes))
    receipt = 'project/jobs/export-name-'+kind+'-'+identity+'.json'
    def choice(ordinal):
        if type(ordinal) is not int or not 1 <= ordinal <= 10001:
            raise ValueError('Invalid export name ordinal.')
        name = label+('' if ordinal == 1 else '_'+str(ordinal))
        base = 'exports/'+kind+'/'+name
        for suffix in suffixes or ('/frame_99999999.png',):
            policy.check_budget(base+suffix)
        claim = 'project/jobs/export-claim-'+state._hash(base.casefold().encode())+'.json'
        return dict(request=request, ordinal=ordinal, base=base, claim=claim)
    with state._lock(project):
        receipt_path = confined(project, receipt)
        if receipt_path.exists():
            saved = state._decode(state._read_bytes(receipt_path))
            expected = choice(saved.get('ordinal'))
            if saved != expected or state._decode(state._read_bytes(confined(project, saved['claim']))) != expected:
                raise state.StateConflict('Export name reservation differs from its owner.')
            return saved['base']
        parent = confined(project, 'exports/'+kind)
        occupied = {p.name.casefold() for p in parent.iterdir()} if parent.exists() else set()
        for ordinal in range(1,10002):
            saved = choice(ordinal)
            claim_path = confined(project, saved['claim'])
            if claim_path.exists():
                if state._decode(state._read_bytes(claim_path)) != saved:
                    continue
            else:
                name = PurePosixPath(saved['base']).name.casefold()
                if name in occupied or any(name+suffix.casefold() in occupied for suffix in suffixes):
                    continue
                # Claim and receipt use the existing no-replace, durable writer.
                state._immutable(project, saved['claim'], state._encode(saved), budget)
            state._immutable(project, receipt, state._encode(saved), budget)
            return saved['base']
    raise ValueError('No unused export name is available; existing exports were retained.')


def frame_directory(payloads, logical):
    """Resolve an existing sequence from its accepted index, never from its ID."""
    parents = {str(PurePosixPath(record['file']['path']).parent)
               for address, record in payloads.items()
               if address.startswith(logical+'/') and re.fullmatch(
                   r'frame_[0-9]{8,}\.png', address[len(logical)+1:])}
    if not parents:
        return None
    if len(parents) != 1:
        raise state.StateConflict('PNG sequence spans multiple physical directories.')
    parent = parents.pop()
    if len(PurePosixPath(parent).parts) != 3 or not parent.startswith('exports/png/'):
        raise state.StateConflict('PNG sequence is outside its export directory.')
    return parent
