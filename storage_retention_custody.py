"""Witness pre-existing payload damage during explicit catalogue retirement.

This never repairs a saved hash, modifies media, or authorizes deletion. The
domain service proves ownership; quarantine records the exact observed state.
Undo restores that same catalogue state, including the original recorded hash.
Missing bytes remain missing and edited bytes remain explicitly unverifiable
against the original output. Physical purge is not part of this operation.
"""
import re

if __package__:
    from . import storage_project as project, storage_state as state, storage_resolver as resolver
else:
    import storage_project as project
    import storage_state as state
    import storage_resolver as resolver


def observe(root, record, permitted=()):
    """Return a custody exception and a fresh signature; I/O failures propagate."""
    project._record(record)
    if not isinstance(permitted, (list, tuple)) or set(permitted)-{'edited', 'missing'}:
        raise ValueError('Unsupported payload custody permission.')
    path = resolver.confined(root, record['file']['path'])
    try:
        digest, signature = project._hash_file(path)
    except FileNotFoundError:
        # Absence, not a permission error, unreadable file, symlink or directory.
        try:
            path.lstat()
        except FileNotFoundError:
            if 'missing' in permitted:
                return dict(status='missing'), (path, None)
        raise
    actual = dict(path=record['file']['path'], sha256=digest, size=signature[2])
    if actual != record['file']:
        if 'edited' not in permitted:
            raise state.StateConflict('Payload checksum/size differs from accepted descriptor: '+record['address'])
        return dict(status='edited', file=actual), (path, signature)
    return None, (path, signature)


def verify(root, record, custody):
    if not isinstance(custody, dict) or custody.get('status') not in ('edited', 'missing'):
        raise ValueError('Invalid payload custody witness.')
    actual, signature = observe(root, record, [custody['status']])
    if actual != custody:
        raise state.StateConflict('Payload custody changed; refresh the deletion/undo preview: '+record['address'])
    return signature


def signature_unchanged(root, path, signature):
    # Revalidate confinement as well: an absent path must not become a link.
    resolver.confined(root, path.relative_to(root).as_posix())
    if signature is not None:
        return resolver._signature(path) == signature
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    return False


def candidate_exceptions(candidate, previous):
    """Only a new, exact quarantine/undo receipt may preserve damaged state.

    Public payload/control commits cannot create retention receipts. Check the
    accepted descriptors and full-root transition again here, at publication,
    rather than trusting a caller flag or swallowing checksum exceptions.
    """
    current, old = candidate.state, previous.state
    exceptions = {}
    for address in current['documents'].keys()-old['documents'].keys():
        match = re.fullmatch(r'retention/([0-9a-f]{32})/receipt\.json', address)
        if not match:
            continue
        operation = match[1]
        receipt = state._decode(candidate.read(address))
        if (receipt.get('format') != 'h3_catalogue_quarantine_receipt_v1'
                or receipt.get('operation_id') != operation
                or operation not in current['operations'] or operation in old['operations']):
            raise state.StateConflict('Unwitnessed payload custody transition.')
        action = receipt.get('action')
        if action == 'quarantine':
            preview = receipt['preview']
            if preview['base'] != previous.reference:
                raise state.StateConflict('Payload custody requires the exact retirement base.')
            source = previous
        elif action == 'undo':
            if receipt['base'] != previous.reference:
                raise state.StateConflict('Payload custody requires the exact undo base.')
            original = state._token(receipt['quarantine_operation'])
            retired = state._decode(previous.read('retention/'+original+'/receipt.json'))
            if (retired.get('action') != 'quarantine' or retired.get('operation_id') != original
                    or state._hash(state._encode(retired)) != receipt['quarantine_sha256']):
                raise state.StateConflict('Undo custody differs from the original quarantine.')
            preview = retired['preview']
            source = state.Snapshot(candidate.project, preview['base'])
        else:
            raise ValueError('Unsupported payload custody action.')
        hashed = {key:value for key,value in preview.items() if key != 'sha256'}
        if state._hash(state._encode(hashed)) != preview['sha256']:
            raise state.StateConflict('Payload custody preview digest differs.')
        source_descriptors = source.state['documents']
        for item in preview['items']:
            if 'custody' not in item:
                continue
            key = project.payload_key(item['address'])
            if (item['key'] != key or source_descriptors.get(key) != item['descriptor']
                    or project._indexed_record(source, key) != item.get('payload')):
                raise state.StateConflict('Payload custody does not match its original descriptor.')
            if action == 'quarantine':
                valid = key not in current['documents'] and old['documents'].get(key) == item['descriptor']
            else:
                valid = key not in old['documents'] and current['documents'].get(key) == item['descriptor']
            if not valid or item['address'] in exceptions:
                raise state.StateConflict('Payload custody is not an exact retirement/restoration.')
            exceptions[item['address']] = verify(candidate.project, item['payload'], item['custody'])
    return exceptions
