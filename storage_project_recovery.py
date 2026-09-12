"""Recover the CURRENT accepted combined state into an independent V1 tree.

Current logical files are restored; old versions, immutable roots and uncertain
staging are kept separately as recovery evidence, never made active by guessing.
"""
if __package__:
    from . import storage_project as project, storage_state as state, storage_recovery as recovery
    from . import storage_resolver as resolver
else:
    import storage_project as project
    import storage_state as state
    import storage_recovery as recovery
    import storage_resolver as resolver


def validate_source(source, plan):
    if plan['source_storage_format'] != project.FORMAT:
        raise ValueError('Unsupported combined recovery source format.')
    # Requires explicit control_rehearsal_access and an ungated current root.
    # Current bytes are frozen by the normal recovery hash/namespace witness.
    return project.ProjectStore(source).snapshot()


def capture_project(source, store):
    if not isinstance(store, project.ProjectStore) or store.project != source:
        raise ValueError('Combined recovery requires this copy\'s explicit ProjectStore.')
    snapshot = store.snapshot()
    root = snapshot.state
    catalogue = project.payload_catalog(snapshot)
    accepted = {}
    for logical, descriptor in root['documents'].items():
        if logical.startswith(('__storage__/', '__migration__/')):
            continue
        path = descriptor['file']['path']
        if path in accepted:
            raise ValueError('Combined recovery cannot infer a shared control-file owner.')
        accepted[path] = (logical, 'control', descriptor['file'])
    for logical, record in catalogue.items():
        path = record['file']['path']
        if path in accepted:
            raise ValueError('Combined recovery has ambiguous file ownership.')
        accepted[path] = (logical, 'payload', record['file'])
    physical = recovery._files(source)
    if set(accepted) - set(physical):
        raise ValueError('Accepted combined files are missing; recovery will not invent replacements.')
    rows, locks, signatures, names = [], [], {}, set()
    prefix = 'h3_chains/'+source.name+'/'
    for address, path in sorted(physical.items()):
        if address.endswith('.lock'):
            locks.append(address)
            continue
        digest, signature = recovery._digest_stable(path)
        signatures[address] = signature
        if address in accepted:
            logical, role, reference = accepted[address]
            if digest != reference['sha256'] or signature[2] != reference['size']:
                raise state.StateConflict('Accepted combined file differs from its root witness.')
            target = prefix+logical
        else:
            # Preserve historical authority and any unaccepted leftover bytes.
            # They never enter the restored run or satisfy a runtime file read.
            target, role = recovery.RECOVERY+'/authority/'+address, 'authority'
        if target.casefold() in names:
            raise ValueError('Case-colliding combined recovery destination.')
        names.add(target.casefold())
        rows.append({'source': address, 'target': target, 'sha256': digest,
                     'size': signature[2], 'role': role})
    if __package__:
        from .storage_retention_portability import capture, entries
    else:
        from storage_retention_portability import capture, entries
    extra,portable = capture(store,snapshot,rows)
    generated = entries(dict(source=str(source),portable_retention=portable))
    for row in extra+generated:
        if row['target'].casefold() in names:
            raise ValueError('Portable custody collides with a recovered file.')
        names.add(row['target'].casefold())
    rows.extend(extra)
    for name in names:
        parts = name.split('/')
        if any('/'.join(parts[:i]) in names for i in range(1, len(parts))):
            raise ValueError('File/directory combined recovery collision.')
    snapshot.verify()
    current = recovery._files(source)
    if (set(current) != set(signatures) | set(locks) or any(
            resolver._signature(current[name]) != signature for name, signature in signatures.items())):
        raise state.StateConflict('Combined state changed during recovery preparation.')
    if store.snapshot().reference != snapshot.reference:
        raise state.StateConflict('Combined root changed during recovery preparation.')
    return rows, locks, {'source_storage_format': project.FORMAT, 'source_root': snapshot.reference,
                        'portable_retention': portable}
