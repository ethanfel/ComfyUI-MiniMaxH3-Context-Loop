"""Portable, hash-bound custody for pending organized quarantines.

Recovery keeps the exact pre/post transaction records and required file bytes
inside the recovered project. Shared objects are content-addressed; no roots
are activated, no artifact is restored and no damaged file is called intact.
"""
from pathlib import Path
import re

if __package__:
    from . import storage_state as state, storage_project as project, storage_resolver as resolver
    from .storage_quarantine import ProjectQuarantine, _address, RECEIPT, STATUS, PREVIEW, PREVIEW_UPDATES, PREVIEW_ARCHIVES
else:
    import storage_state as state
    import storage_project as project
    import storage_resolver as resolver
    from storage_quarantine import ProjectQuarantine, _address, RECEIPT, STATUS, PREVIEW, PREVIEW_UPDATES, PREVIEW_ARCHIVES

FORMAT = 'h3_portable_quarantine_v1'


def address(operation):
    return _address(operation,'portable')


def _identity(reference):
    if (not isinstance(reference,dict) or set(reference) != {'path','sha256','size'}
            or resolver.artifact_address(reference['path']) != reference['path']
            or not re.fullmatch('[0-9a-f]{64}',str(reference['sha256']))
            or type(reference['size']) is not int or reference['size'] < 0):
        raise ValueError('Invalid portable custody file reference.')
    return state._hash(state._encode(reference))


def _bytes(reference):
    return {key:reference[key] for key in ('sha256','size')}


class PortableQuarantine:
    """Read-only proof of a quarantine, independent of old physical roots."""

    def __init__(self, run, operation, read, path):
        self.run,self.operation,self.read,self.locate = run,state._token(operation),read,path
        self.value = state._decode(read(address(operation)))
        value = self.value
        if (set(value) != {'format','run_name','operation_id','before','published','receipt_sha256','references'}
                or value['format'] != FORMAT or value['run_name'] != run or value['operation_id'] != operation
                or not isinstance(value['references'],list)):
            raise state.StateConflict('Portable quarantine identity differs from its project or operation.')
        self.references,self.used = {},set()
        for item in value['references']:
            if not isinstance(item,dict) or set(item) != {'reference','object','bytes'}:
                raise ValueError('Invalid portable quarantine object witness.')
            key = _identity(item['reference'])
            if key in self.references:
                raise ValueError('Repeated portable quarantine reference.')
            witness = item['bytes']
            if witness is None:
                if item['object'] is not None:
                    raise ValueError('Missing custody cannot nominate an object.')
            elif (not isinstance(witness,dict) or set(witness) != {'sha256','size'}
                    or not re.fullmatch('[0-9a-f]{64}',str(witness['sha256']))
                    or type(witness['size']) is not int or witness['size'] < 0
                    or item['object'] != 'retention/objects/'+witness['sha256']):
                raise ValueError('Portable custody object does not match its hash witness.')
            self.references[key] = item
        for key in ('before','published'):
            state._validate_reference(value[key],r'project/roots/[0-9a-f]{32}\.json')
        self.before = self.document(value['before'])
        self.published = self.document(value['published'])
        for root in (self.before,self.published):
            if (root.get('format') != state.ROOT or root.get('run_name') != run
                    or not isinstance(root.get('documents'),dict) or not isinstance(root.get('operations'),dict)):
                raise state.StateConflict('Portable quarantine contains an invalid historical root.')
        self.record = state._decode(read(_address(operation)))
        if (state._hash(read(_address(operation))) != value['receipt_sha256']
                or self.record.get('format') != RECEIPT or self.record.get('action') != 'quarantine'
                or self.record.get('operation_id') != operation):
            raise state.StateConflict('Portable quarantine differs from the original receipt.')
        self.preview = self.record['preview']
        preview = self.preview
        keys = {'format','project','base','items','reason','sha256'}
        if preview.get('format') in (PREVIEW_UPDATES,PREVIEW_ARCHIVES):
            keys.add('updates')
        if preview.get('format') == PREVIEW_ARCHIVES:
            keys.add('archives')
        if (preview.get('format') not in (PREVIEW,PREVIEW_UPDATES,PREVIEW_ARCHIVES) or set(preview) != keys
                or not isinstance(preview['items'],list) or not preview['items']
                or not isinstance(preview.get('updates',[]),list) or not isinstance(preview.get('archives',[]),list)
                or not isinstance(preview['reason'],str) or not preview['reason'].strip() or len(preview['reason']) > 4096):
            raise ValueError('Unsupported portable quarantine preview.')
        if (preview.get('project') != run or preview.get('base') != value['before']
                or state._hash(state._encode({k:v for k,v in preview.items() if k != 'sha256'})) != preview.get('sha256')
                or self.published.get('parent') != value['before']
                or self.published.get('generation') != self.before.get('generation',-2)+1
                or operation in self.before['operations'] or operation not in self.published['operations']):
            raise state.StateConflict('Portable quarantine is not the accepted pre/post transaction.')
        before,after = self.before['documents'],self.published['documents']
        if self.document(after[_address(operation)]['file']) != self.record:
            raise state.StateConflict('Historical root did not accept this quarantine receipt.')
        if self.document(after[_address(operation,'state')]['file']) != dict(
                format=STATUS,status='quarantined',operation_id=operation):
            raise state.StateConflict('Historical root has no quarantined state.')
        items,updates,archives = preview['items'],preview.get('updates',[]),preview.get('archives',[])
        removed,changed = set(),{_address(operation),_address(operation,'state')}
        def user_identity(key):
            state._logical(key)
            if key.startswith(('__storage__/','__migration__/','retention/','jobs/','orchestration/')):
                raise state.StateConflict('Portable quarantine cannot replace internal authority.')
        for item in items:
            key = item['key']
            user_identity(item['address']); state._descriptor(item['descriptor'])
            if key in removed or before.get(key) != item['descriptor'] or key in after:
                raise state.StateConflict('Portable quarantine changed its retired descriptors.')
            removed.add(key)
            raw = self.data(item['descriptor']['file'])
            if 'payload' in item:
                record = project._record(state._decode(raw))
                if key != project.payload_key(item['address']) or record != item['payload'] or record['address'] != item['address']:
                    raise state.StateConflict('Portable quarantine has a mismatched payload identity.')
                self.file(record['file'],custody=item.get('custody'))
            elif key != item['address'] or 'custody' in item:
                raise state.StateConflict('Control custody cannot bypass metadata verification.')
        for item in updates:
            key = item['address']
            user_identity(key)
            if key in changed or key in removed or before.get(key) != item['descriptor']:
                raise state.StateConflict('Portable quarantine changed its update descriptors.')
            changed.add(key)
            self.data(item['descriptor']['file'])
            if (self.data(after[key]['file']) != bytes.fromhex(item['data_hex'])
                    or any(after[key][field] != item['descriptor'][field] for field in ('scope','category','immutable'))):
                raise state.StateConflict('Portable quarantine update differs from its accepted bytes.')
        for item in archives:
            key = item['address']
            user_identity(key)
            if (key in changed or key in before or item['source'] not in removed
                    or before[item['source']] != item['descriptor'] or after.get(key) != item['descriptor']):
                raise state.StateConflict('Portable quarantine archive differs from its exact source.')
            changed.add(key)
            self.data(item['descriptor']['file'])
        if set(after) != (set(before)-removed)|changed or any(
                after[key] != before[key] for key in set(before)-removed-changed):
            raise state.StateConflict('Portable quarantine contains unrelated root changes.')
        if self.used != set(self.references):
            raise state.StateConflict('Portable quarantine includes unrelated file evidence.')

    def file(self, reference, *, custody=None):
        key = _identity(reference)
        item = self.references.get(key)
        if item is None:
            raise state.StateConflict('Portable quarantine is missing a required file witness.')
        self.used.add(key)
        expected = _bytes(reference)
        if custody is not None:
            if custody == {'status':'missing'}:
                expected = None
            elif (set(custody) == {'status','file'} and custody['status'] == 'edited'
                    and custody['file']['path'] == reference['path']):
                _identity(custody['file'])
                expected = _bytes(custody['file'])
            else:
                raise ValueError('Unsupported portable damaged-file witness.')
        if item['bytes'] != expected:
            raise state.StateConflict('Portable custody differs from its recorded condition.')
        if expected is None:
            return None
        path = self.locate(item['object'])
        digest,signature = project._hash_file(path)
        if dict(sha256=digest,size=signature[2]) != expected:
            raise state.StateConflict('Portable quarantine object is missing or changed.')
        return path

    def data(self, reference):
        raw = state._read_bytes(self.file(reference))
        if _bytes(reference) != dict(sha256=state._hash(raw),size=len(raw)):
            raise state.StateConflict('Portable quarantine control changed during reading.')
        return raw

    def document(self, reference):
        return state._decode(self.data(reference))

    @classmethod
    def from_store(cls, store, snapshot, operation):
        return cls(store.project.name,operation,snapshot.read,
                   lambda key:store.payload_path(snapshot,key,verify=True))

    @classmethod
    def from_legacy(cls, root, operation):
        root = Path(root)
        return cls(root.name,operation,lambda key:state._read_bytes(resolver.confined(root,key)),
                   lambda key:resolver.confined(root,key))


def capture(store, snapshot, rows):
    """Return explicit extra byte copies and generated recovery controls."""
    descriptors = snapshot.state['documents']
    operations = snapshot.state['operations']
    destinations = {row['target']:row for row in rows}
    extra,controls = [],{}
    prefix = 'h3_chains/'+store.project.name+'/'
    for key in sorted(descriptors):
        match = re.fullmatch(r'retention/([0-9a-f]{32})/receipt\.json',key)
        if not match:
            continue
        operation = match[1]
        record = state._decode(snapshot.read(key))
        if record.get('format') != RECEIPT or record.get('action') != 'quarantine':
            continue
        if address(operation) in descriptors:
            PortableQuarantine.from_store(store,snapshot,operation)
            continue
        status = state._decode(snapshot.read(_address(operation,'state')))
        if status.get('status') == 'restored':
            continue  # History remains copied; this receipt no longer offers undo.
        if status != dict(format=STATUS,status='quarantined',operation_id=operation):
            raise state.StateConflict('Quarantine has an unknown custody state.')
        if operation not in operations:
            raise state.StateConflict('Imported quarantine has no portable custody; recover its preserved authority first.')
        kernel = ProjectQuarantine(store)
        before = kernel._preview(record['preview'])
        published = store.committed_snapshot(operations[operation])
        if published.read(key) != snapshot.read(key):
            raise state.StateConflict('Quarantine receipt differs from its accepted transaction.')
        refs = {}
        def add(reference, custody=None):
            identity = _identity(reference)
            expected = None if custody == {'status':'missing'} else _bytes(
                custody['file'] if custody else reference)
            value = dict(reference=reference,object='retention/objects/'+expected['sha256'] if expected else None,
                         bytes=expected)
            if identity in refs and refs[identity] != value:
                raise state.StateConflict('A quarantine has contradictory file custody.')
            refs[identity] = value
            source = resolver.confined(store.project,reference['path'])
            if expected is None:
                if source.exists():
                    raise state.StateConflict('Missing quarantine payload appeared during recovery.')
                return
            digest,signature = project._hash_file(source)
            if dict(sha256=digest,size=signature[2]) != expected:
                raise state.StateConflict('Retained quarantine bytes changed before recovery.')
            target = prefix+value['object']
            row = dict(source=reference['path'],target=target,sha256=digest,size=signature[2],role='payload')
            if target in destinations:
                if _bytes(destinations[target]) != expected:
                    raise state.StateConflict('Portable custody destination is occupied by different bytes.')
            else:
                destinations[target] = row
                extra.append(row)
        add(before.reference); add(published.reference)
        for suffix in ('receipt','state'):
            add(published.state['documents'][_address(operation,suffix)]['file'])
        for item in record['preview']['items']:
            add(item['descriptor']['file'])
            if 'payload' in item:
                add(item['payload']['file'],item.get('custody'))
        for item in record['preview'].get('updates',[]):
            add(item['descriptor']['file']); add(published.state['documents'][item['address']]['file'])
        for item in record['preview'].get('archives',[]):
            add(item['descriptor']['file'])
        value = dict(format=FORMAT,run_name=store.project.name,operation_id=operation,
            before=before.reference,published=published.reference,receipt_sha256=state._hash(snapshot.read(key)),
            references=[refs[key] for key in sorted(refs)])
        # Use the same reader to prove the closure before making a journal.
        lookup = {item['object']:resolver.confined(store.project,item['reference']['path'])
                  for item in refs.values() if item['object']}
        PortableQuarantine(store.project.name,operation,
            lambda name:state._encode(value) if name == address(operation) else snapshot.read(name),lookup.__getitem__)
        controls[address(operation)] = value
    return extra,controls


def entries(plan):
    controls = plan.get('portable_retention',{})
    if not isinstance(controls,dict):
        raise ValueError('Portable recovery controls must be an explicit inventory.')
    rows = []
    run = Path(plan['source']).name
    for key,value in sorted(controls.items()):
        if (not isinstance(value,dict) or value.get('format') != FORMAT or value.get('run_name') != run
                or key != address(value.get('operation_id'))):
            raise ValueError('Portable recovery inventory belongs to another operation/project.')
        raw = state._encode(value)
        if len(raw) > 32*1024*1024:
            raise ValueError('Portable custody control exceeds the supported size.')
        rows.append(dict(source=key,target='h3_chains/'+run+'/'+key,role='control',
                         sha256=state._hash(raw),size=len(raw)))
    return rows
