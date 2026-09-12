"""Journaled joining of an independent control copy and its source payloads.

This is the explicit copy-only intermediate service. It never changes the
receipted source, selects a live chain, or rewrites legacy JSON/hash inputs.
The target is gated while copying and becomes combined state in one publication.
"""
from contextlib import ExitStack
import copy
import os
from pathlib import Path
import re
import shutil
import uuid

if __package__:
    from . import storage_state as state, storage_project as project
    from . import storage_resolver as resolver, storage_recovery as recovery
    from . import storage_journal as progress
    from .storage_rehearsal import _copy_root, _lock
    from .processing_persistence import atomic_json, sync_directory, publish_new_file
    from .storage_layout import OrganizedStorageLayout
else:
    import storage_state as state
    import storage_project as project
    import storage_resolver as resolver
    import storage_recovery as recovery
    import storage_journal as progress
    from storage_rehearsal import _copy_root, _lock
    from processing_persistence import atomic_json, sync_directory, publish_new_file
    from storage_layout import OrganizedStorageLayout

PLAN = 'h3_project_join_plan_v1'
JOURNAL = 'h3_project_join_journal_v1'


def _chapter_import_order(source, base, rows):
    if __package__:
        from . import storage_chapter_index as index
    else:
        import storage_chapter_index as index
    groups, times, histories, partial_indexes = {}, {}, {}, set()
    documents = base.state['documents']
    prefix = 'h3_chains/'+source.name+'/'
    def read(address):
        if address not in documents:
            raise FileNotFoundError(address)
        return base.read(address)
    for key, descriptor in documents.items():
        match = index.HEAD_PATTERN.fullmatch(key)
        if match is None:
            continue
        branch_prefix, number = match['prefix'] or '', int(match['number'])
        scope = 'branch:'+('main' if not branch_prefix else branch_prefix.split('/')[1])
        if (number < 1 or key != index.head(branch_prefix, number)
                or descriptor['scope'] != scope or descriptor['category'] != 'cuts'
                or descriptor['immutable']):
            raise ValueError('Imported chapter selector must be a mutable branch-owned cut index.')
        value = state._decode(read(key))
        histories[key] = index.verified_history(value, branch_prefix, number, source.name, read)
        if 'history' not in value:
            partial_indexes.add(key)
    for row in rows:
        address = row['target'].removeprefix(prefix)
        if row['role'] != 'control' or not index.PATTERN.fullmatch(address):
            continue
        branch_prefix, number, _ = index.parts(address)
        raw = base.read(address)
        index.validate(raw, address, source.name)
        key = index.head(branch_prefix, number)
        if any(item['manifest'] == address for item in histories.get(key, ())):
            # Preserve indexed order even when reverse-copy file times change.
            continue
        if key in partial_indexes:
            raise ValueError('Chapter selector lacks complete publication history; cannot order additional snapshots safely.')
        path = resolver.confined(source, row['source'])
        before = path.stat().st_mtime_ns
        if recovery.sha256(path) != row['sha256'] or path.stat().st_mtime_ns != before:
            raise state.StateConflict('Chapter changed while freezing its import order.')
        times[row['source']] = before
        groups.setdefault(key, (number, []))[1].append((before, address, index.entry(address, raw)))
    # Ordinary legacy writers can add seals after recovery without updating
    # the portable index. Freeze those new seals after the indexed history.
    return {key:index.record(number, histories.get(key, [])+[item[2] for item in sorted(items)])
            for key,(number,items) in groups.items()}, times


def _verify_chapter_order_source(plan, source):
    for address, timestamp in plan.get('chapter_order_mtimes', {}).items():
        if resolver.confined(source, address).stat().st_mtime_ns != timestamp:
            raise state.StateConflict('Chapter order changed after migration preparation; prepare again.')


def _locks(source, destination):
    stack = ExitStack()
    try:
        for root in sorted((source, destination)):
            stack.enter_context(_lock(root))
    except BaseException:
        stack.close()
        raise
    return stack


def _scope_paths(receipt, destination, folder, *, protocol=None):
    progress.protocol(protocol)
    receipt = Path(receipt).absolute()
    source, _ = _copy_root(receipt)
    lab = receipt.parent
    destination, folder = Path(destination).absolute(), Path(folder).absolute()
    if destination.parent.name != 'h3_chains' or destination.name != source.name:
        raise ValueError('Joined copy must retain the receipted project identity.')
    for path in (destination, folder):
        if (path == lab or not path.is_relative_to(lab) or path.is_relative_to(source.parent.parent)
                or source.is_relative_to(path)):
            raise ValueError('Join target and journal must stay in the independent-copy lab.')
        resolver.confined(lab, path.relative_to(lab).as_posix())
        if protocol is None:
            state.require_atomic_control_files(path)
    if folder.is_relative_to(destination.parent.parent) or destination.is_relative_to(folder):
        raise ValueError('Join journal must be outside the target output.')
    return source, destination, folder


def prepare_join(receipt_path, control_store, journal_directory, payload_targets):
    """Freeze exact source bytes and an explicit logical->physical payload plan.

    The control copy must still be an exact import of all current source JSON/
    text records. Unknown targets are never guessed; every binary needs a
    caller-reviewed organized destination and ownership scope.
    payload_targets[address] = {target: organized path, scope: owner,
                                immutable: bool}
    """
    if type(control_store) is not state.ControlStore:
        raise ValueError('Join requires a fresh control-only copy, not an existing combined project.')
    protocol = progress.bootstrap(control_store.project/'storage.json')[0].get('commit_protocol')
    source, destination, folder = _scope_paths(receipt_path, control_store.project, journal_directory,
                                              protocol=protocol)
    if folder.exists():
        raise FileExistsError('Use a new join journal; existing evidence is retained.')
    with _locks(source, destination), resolver.rehearsal_access(source):
        marker, raw, base = control_store._marker()
        if protocol is not None:
            control_store._acknowledge_commit()
        root = base.state
        if root.get('import_source') != str(source):
            raise ValueError('Control copy was imported from a different source.')
        rows, locks = recovery._capture(source)
        prefix = 'h3_chains/'+source.name+'/'
        controls, payloads, authorities = [], [], []
        policy = OrganizedStorageLayout(str(destination), marker['path_budget'])
        mappings = copy.deepcopy(payload_targets)
        for row in rows:
            if row['role'] == 'authority':
                authorities.append(row)
                continue
            if not row['target'].startswith(prefix):
                raise ValueError('Join source identity escapes its project.')
            address = row['target'][len(prefix):]
            if address.startswith(('__storage__/', '__migration__/')):
                raise ValueError('Source occupies a reserved migration identity.')
            if row['role'] == 'control':
                if address not in root['documents'] or state._hash(base.read(address)) != row['sha256']:
                    raise ValueError('Control import is missing or differs from current source: '+address)
                controls.append(address)
                continue
            request = mappings.pop(address, None)
            if not isinstance(request, dict) or set(request) != {'target', 'scope', 'immutable'}:
                raise ValueError('Every source payload needs an explicit destination/owner: '+address)
            record = project._record({'format': project.PAYLOAD, 'address': address,
                'scope': request['scope'], 'immutable': request['immutable'],
                'file': {'path': request['target'], 'sha256': row['sha256'], 'size': row['size']}})
            policy.check_budget(record['file']['path'])
            path = resolver.confined(destination, record['file']['path'])
            if path.exists():
                raise FileExistsError('Unowned payload destination already exists: '+str(path))
            payloads.append({'source': row['source'], 'record': record})
        if mappings or set(controls) != set(root['documents']):
            raise ValueError('Join inventory must exactly cover source payloads and imported controls.')
        # Resolve all planned logical/physical collisions before creating a gate.
        targets = {item['record']['file']['path'].casefold() for item in payloads}
        if len(targets) != len(payloads):
            raise ValueError('Join targets have duplicate/case-colliding owners.')
        occupied = {p.casefold() for p in recovery._files(destination)}
        for entries in (targets | occupied, {p.casefold() for p in controls} |
                        {item['record']['address'].casefold() for item in payloads}):
            for name in entries:
                parts = name.split('/')
                if any('/'.join(parts[:i]) in entries for i in range(1, len(parts))):
                    raise ValueError('Join contains a file/directory collision.')
        base.verify()
        chapter_heads, chapter_times = _chapter_import_order(source, base, rows)
        for address, value in chapter_heads.items():
            target = 'project/cuts/'+state._hash(state._encode([address, value]))[:32]+'.json'
            policy.check_budget(target)
            if target.casefold() in targets | occupied or address.casefold() in {
                    item['record']['address'].casefold() for item in payloads}:
                raise ValueError('Derived chapter selector collides with an imported file.')
        derived = {'project/cuts/'+state._hash(state._encode([address, value]))[:32]+'.json'
                   for address, value in chapter_heads.items()}
        for entries in (targets | occupied | derived, {p.casefold() for p in controls} |
                        {p.casefold() for p in chapter_heads} |
                        {item['record']['address'].casefold() for item in payloads}):
            for name in entries:
                parts = name.split('/')
                if any('/'.join(parts[:i]) in entries for i in range(1, len(parts))):
                    raise ValueError('Derived chapter selector has a file/directory collision.')
        identifier = uuid.uuid4().hex
        plan = {'format': PLAN, 'operation_id': identifier, 'receipt': str(Path(receipt_path).absolute()),
                'source': str(source), 'destination': str(destination), 'base': base.reference,
                'marker_sha256': state._hash(raw), 'path_budget': marker['path_budget'],
                'rows': rows, 'excluded_locks': locks, 'payloads': payloads, 'authorities': authorities,
                'initial_files': {name: recovery.sha256(path) for name, path in recovery._files(destination).items()}}
        if __package__:
            from . import storage_ownership_recovery
        else:
            import storage_ownership_recovery
        plan['external_ownership'] = storage_ownership_recovery.capture(source)
        if chapter_heads:
            plan.update(chapter_heads=chapter_heads, chapter_order_mtimes=chapter_times)
        if protocol is not None:
            plan.update(commit_protocol=protocol, log_base_sequence=progress.CommitLog(destination,
                state._read_bytes(destination/'storage.json')).read().sequence)
            journal_policy = OrganizedStorageLayout(str(folder), marker['path_budget'])
            for address in ('plan.json', 'journal.json', 'project/commits/000000000001.ack.json',
                            'partials/'+('f'*32)+'.part'):
                journal_policy.check_atomic_json_budget(address)
        folder.mkdir(parents=True)
        sync_directory(folder.parent)
        if protocol is None:
            atomic_json(folder/'plan.json', plan)
        else:
            progress.publish_once(folder/'plan.json', plan, path_budget=marker['path_budget'])
        journal = folder/'journal.json'
        initial = {'format': JOURNAL, 'phase': 'prepared', 'operation_id': identifier,
                   'plan_sha256': recovery.sha256(folder/'plan.json')}
        if protocol is None:
            atomic_json(journal, initial)
        else:
            progress.create(journal, initial, path_budget=marker['path_budget'])
        return journal


def _advance_journal(path, journal, **changes):
    updated = dict(journal, **changes)
    if journal.get('commit_protocol') is None:
        atomic_json(path, updated)
    else:
        progress.advance(path, journal, updated)
    return updated


def _load(journal_path):
    path = Path(journal_path).absolute()
    resolver.confined(path.parent, path.name)
    journal, _ = progress.bootstrap(path)  # read-only until the lab is validated
    raw = state._read_bytes(resolver.confined(path.parent, 'plan.json'))
    plan = state._decode(raw)
    if (journal.get('format') != JOURNAL or plan.get('format') != PLAN
            or journal.get('operation_id') != plan.get('operation_id')
            or state._hash(raw) != journal.get('plan_sha256')):
        raise ValueError('Changed or invalid immutable join plan.')
    state._token(plan['operation_id'])
    protocol = progress.protocol(plan.get('commit_protocol'))
    if journal.get('commit_protocol') != protocol:
        raise ValueError('Join journal and plan use different commit protocols.')
    source, destination, folder = _scope_paths(plan['receipt'], plan['destination'], path.parent,
                                               protocol=protocol)
    if plan['source'] != str(source):
        raise ValueError('Join source no longer matches the independent-copy receipt.')
    if protocol is not None:
        if journal.get('path_budget') != plan['path_budget']:
            raise ValueError('Join journal budget differs from the immutable plan.')
        current = progress.read(path)
        if any(current.get(key) != journal.get(key) for key in progress._IDENTITY):
            raise ValueError('Immutable join journal identity changed.')
        journal = current
    return path, journal, plan, source, destination


def _resume_ready(store, marker, journal_path, journal, plan):
    if marker.get('format') != project.FORMAT or marker.get('phase') != 'ready':
        return None
    snapshot = store.snapshot()
    receipt = snapshot.state['operations'].get(plan['operation_id'])
    if not receipt or receipt['request_sha256'] != journal['plan_sha256']:
        raise state.StateConflict('Combined target belongs to another migration; no overwrite.')
    # A lost acknowledgement must not restore the import over later user saves.
    store._acknowledge_commit()
    _advance_journal(journal_path, journal, phase='published', published_generation=receipt['generation'])
    sync_directory(store.project)
    return {'project': str(store.project), 'root': snapshot.reference,
            'generation': snapshot.state['generation'], 'payloads': len(project.payload_catalog(snapshot))}


def _target_namespace(plan, destination):
    payload_paths = {row['record']['file']['path'] for row in plan['payloads']}
    metadata = {'project/payloads/'+state._hash(state._encode(row['record']))[:32]+'.json'
                for row in plan['payloads']}
    metadata.update('project/legacy/'+row['sha256'][:32]+'.json' for row in plan['authorities'])
    metadata.update('project/cuts/'+state._hash(state._encode([address, value]))[:32]+'.json'
                    for address, value in plan.get('chapter_heads', {}).items())
    metadata.add('project/roots/'+plan['operation_id']+'.json')
    actual = recovery._files(destination)
    extra = set(actual) - set(plan['initial_files']) - payload_paths - metadata
    if plan.get('commit_protocol') is not None:
        sequence = plan['log_base_sequence']+1
        extra -= {progress.CommitLog._address(sequence), progress.CommitLog._address(sequence, ack=True)}
        extra = {name for name in extra if not _log_partial(name)}
    if extra:
        raise state.StateConflict('Untracked target files block join publication.')


def _log_partial(name):
    return re.fullmatch(r'project/commits/\.tmp-[0-9a-f]{32}', name) is not None


def _join_immutable(destination, address, raw, plan, folder):
    """Keep incomplete metadata bytes in this operation's external journal.

    Deterministic accepted addresses are part of the immutable plan, so an
    interrupted attempt cannot authorize arbitrary metadata-shaped files.
    """
    OrganizedStorageLayout(str(destination), plan['path_budget']).check_atomic_json_budget(address)
    target = resolver.confined(destination, address)
    if target.exists():
        if state._read_bytes(target) != raw:
            raise state.StateConflict('Join metadata collision; existing bytes retained.')
        sync_directory(target.parent)
    else:
        partials = resolver.confined(folder, 'partials')
        state._mkdir(partials, folder)
        temporary = resolver.confined(partials, uuid.uuid4().hex+'.part')
        with temporary.open('xb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        state._mkdir(target.parent, destination)
        publish_new_file(temporary, target)
        sync_directory(target.parent)
    return {'path': address, 'sha256': state._hash(raw), 'size': len(raw)}


class _JoiningStore(project.ProjectStore):
    def __init__(self, destination, final_check, plan, folder):
        super().__init__(destination)
        self.final_check = final_check
        self.plan, self.folder = plan, folder

    def _write_root(self, root, operation, budget):
        return _join_immutable(self.project, 'project/roots/'+operation+'.json', state._encode(root),
                               self.plan, self.folder)

    def _validate_candidate(self, candidate, marker):
        super()._validate_candidate(candidate, marker)
        self.final_check()

    def _acknowledge_commit(self):
        marker, _ = self._authority()
        if marker.get('phase') != 'building':
            return super()._acknowledge_commit()
        if (marker.get('operation_id') != self.plan['operation_id'] or marker.get('root') != self.plan['base']
                or marker.get('plan_sha256') != recovery.sha256(self.folder/'plan.json')):
            raise state.StateConflict('Cannot acknowledge a foreign migration gate.')
        state.Snapshot(self.project, self.plan['base']).verify()
        progress.acknowledge_authority(self.project)


def join_payloads(journal_path, *, after_copy=None):
    """Resume the same join journal; uncertainty never authorizes overwriting."""
    path, journal, plan, source, destination = _load(journal_path)
    store = project.ProjectStore(destination)
    with _locks(source, destination), resolver.rehearsal_access(source):
        loaded = _load(path)
        if loaded[2:] != (plan, source, destination):
            raise state.StateConflict('Join plan changed while waiting for the project locks.')
        journal = loaded[1]
        marker_path = resolver.confined(destination, 'storage.json')
        marker, marker_raw = progress.authority(destination)
        resumed = _resume_ready(store, marker, path, journal, plan)
        if resumed is not None:
            return resumed
        is_gate = (marker.get('format') == project.FORMAT and marker.get('phase') == 'building'
                   and marker.get('operation_id') == plan['operation_id']
                   and marker.get('plan_sha256') == journal['plan_sha256']
                   and marker.get('root') == plan['base'])
        if is_gate and plan.get('commit_protocol') is not None:
            log = progress.CommitLog(destination, state._read_bytes(marker_path))
            if log.read().sequence != plan['log_base_sequence']+1:
                raise state.StateConflict('Join gate occupies an unexpected immutable commit slot.')
        if not is_gate and state._hash(marker_raw) != plan['marker_sha256']:
            raise state.StateConflict('Control target changed after join preparation.')
        if journal.get('phase') == 'published':
            raise state.StateConflict('Published join authority changed; preserve it for inspection.')
        base = state.Snapshot(destination, plan['base'])
        base.verify()
        for name, digest in plan['initial_files'].items():
            if (name != 'storage.json' or plan.get('commit_protocol') is not None) and recovery.sha256(resolver.confined(destination, name)) != digest:
                raise state.StateConflict('Original control copy changed during join.')
        # Copy attempts may have durable immutable document/root candidates.
        # Unknown media or legacy files are never adopted into the final state.
        _target_namespace(plan, destination)
        unowned = set(recovery._files(destination)) - set(plan['initial_files'])
        if plan.get('commit_protocol') is not None:
            unowned = {name for name in unowned if not _log_partial(name)}
        if not is_gate and unowned:
            raise state.StateConflict('Unowned file appeared after join preparation.')
        source_signatures = recovery._verify_source(plan, source)
        _verify_chapter_order_source(plan, source)
        _target_namespace(plan, destination)
        def final_check():
            current = recovery._files(source)
            if ({name for name in current if not name.endswith('.lock')} != set(source_signatures)
                    or any(resolver._signature(current[name]) != signature for name, signature in source_signatures.items())):
                raise state.StateConflict('Source changed during final payload verification.')
            _target_namespace(plan, destination)
            _verify_chapter_order_source(plan, source)
        remaining = sum(row['record']['file']['size'] for row in plan['payloads']
                        if not resolver.confined(destination, row['record']['file']['path']).exists())
        if shutil.disk_usage(destination).free < remaining + 64*1024*1024:
            raise OSError('Not enough free space for independent media copies and metadata headroom.')
        if not is_gate:
            marker.update(format=project.FORMAT, mode=store.MODE, phase='building',
                          operation_id=plan['operation_id'], plan_sha256=journal['plan_sha256'])
            if plan.get('commit_protocol') is None:
                atomic_json(marker_path, marker)
            else:
                progress.advance_authority(destination, marker_raw, marker)
        journal = _advance_journal(path, journal, phase='copying')
        root = base.state
        changed_scopes = set()
        for index, item in enumerate(plan['payloads'], 1):
            record = project._record(item['record'])
            reference = record['file']
            state._mkdir(resolver.confined(destination, reference['path']).parent, destination)
            recovery._copy_row({'source': item['source'], 'target': reference['path'],
                                'sha256': reference['sha256'], 'size': reference['size']}, source, destination, path.parent)
            raw = state._encode(record)
            identity = state._hash(raw)[:32]
            root['documents'][project.payload_key(record['address'])] = {
                'scope': record['scope'], 'category': 'payloads', 'immutable': record['immutable'],
                'file': _join_immutable(destination, 'project/payloads/'+identity+'.json', raw, plan, path.parent)}
            changed_scopes.add(record['scope'])
            if after_copy:
                after_copy(index)
        for item in plan['authorities']:
            raw = state._read_bytes(resolver.confined(source, item['source']))
            if state._hash(raw) != item['sha256']:
                raise state.StateConflict('Source authority changed during join.')
            address = '__migration__/source_authority/'+state._hash(item['source'].encode())+'.json'
            root['documents'][address] = {'scope': 'archive:source', 'category': 'legacy', 'immutable': True,
                'file': _join_immutable(destination, 'project/legacy/'+state._hash(raw)[:32]+'.json', raw, plan, path.parent)}
            changed_scopes.add('archive:source')
        for address, value in plan.get('chapter_heads', {}).items():
            scope = 'branch:'+('main' if not address.startswith('branches/') else address.split('/')[1])
            prior = root['documents'].get(address)
            if prior is not None and (prior['scope'] != scope or prior['category'] != 'cuts' or prior['immutable']):
                raise state.StateConflict('Imported chapter selector would overwrite unrelated accepted work.')
            raw = state._encode(value)
            identity = state._hash(state._encode([address, value]))[:32]
            root['documents'][address] = dict(scope=scope, category='cuts', immutable=False,
                file=_join_immutable(destination, 'project/cuts/'+identity+'.json', raw, plan, path.parent))
            changed_scopes.add(scope)
        # This full rehash proves the source still matches the frozen bytes.
        # Fence final publication against its refreshed attributes, not the
        # pre-copy attributes (which CIFS can refresh while files are opened).
        source_signatures = recovery._verify_source(plan, source)
        for scope in changed_scopes:
            root['scope_revisions'][scope] = state._hash((plan['operation_id']+':'+scope).encode())[:32]
        root.update(epoch=root['epoch']+1, generation=root['generation']+1, parent=base.reference)
        root['operations'][plan['operation_id']] = {'operation_id': plan['operation_id'],
            'generation': root['generation'], 'request_sha256': journal['plan_sha256'],
            'scope_revisions': {scope: root['scope_revisions'][scope] for scope in sorted(changed_scopes)}}
        gate, gate_raw = progress.authority(destination)
        if (gate.get('operation_id') != plan['operation_id'] or gate.get('plan_sha256') != journal['plan_sha256']
                or gate.get('phase') != 'building' or gate.get('root') != plan['base']):
            raise state.StateConflict('Join gate changed outside its lock; no publication.')
        # _publish verifies new payload hashes and controls before the single
        # authority publication. No reader can observe accepted metadata alone.
        _JoiningStore(destination, final_check, plan, path.parent)._publish(
            dict(gate, phase='ready'), gate_raw, root, plan['operation_id'], None)
        return _resume_ready(store, progress.authority(destination)[0], path, journal, plan)
