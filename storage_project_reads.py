"""Pinned, read-only view for explicitly opted-in combined-store consumers.

No normal resolver calls this port. Paths returned here may name immutable
control versions: callers must never give them to a writer or deletion API.
"""
from contextlib import contextmanager, asynccontextmanager
from contextvars import ContextVar
import asyncio
import json
from pathlib import Path
import re

if __package__:
    from . import storage_project as project, storage_state as state, storage_resolver as resolver
    from .branch_scope import branch_id, current_branch
else:
    import storage_project as project
    import storage_state as state
    import storage_resolver as resolver
    from branch_scope import branch_id, current_branch


class ProjectReadView:
    def __init__(self, store, *, base=None):
        if not isinstance(store, project.ProjectStore):
            raise TypeError('Combined read view requires an explicit ProjectStore.')
        if base is not None and (not isinstance(base, state.Snapshot) or base.project != store.project):
            raise ValueError('Read view requires this project\'s pinned root.')
        self.store, self.project, self.base = store, store.project, base
        self.output = self.project.parent.parent
        self._operation = ContextVar('h3_combined_read_view', default=None)
        self._read_traces = ContextVar('h3_combined_read_traces', default=())

    @contextmanager
    def track_reads(self):
        """Collect accepted identities used by one publisher, without broad locks.

        Nested consumers contribute to every active trace. The caller must
        verify bytes and persist these dependencies before accepting outputs.
        This neither changes the pin nor grants write access.
        """
        self._session()
        identities = set()
        token = self._read_traces.set((*self._read_traces.get(), identities))
        try:
            yield identities
        finally:
            self._read_traces.reset(token)

    def _track(self, address):
        session = self._session()
        if address in session['physical']:
            key = address if address in session['controls'] else project.payload_key(address)
            for trace in self._read_traces.get():
                trace.add(key)

    def _prepare_operation(self):
        current = self.store.snapshot()  # check current access/format/gate
        snapshot = self.base or current
        if self.base is not None:
            self.store.validate_snapshot(snapshot, current=current)
        documents = snapshot.state['documents']
        controls = {p: d for p, d in documents.items() if not p.startswith(('__storage__/', '__migration__/'))}
        payloads = project.payload_catalog(snapshot)
        physical = {p: d['file']['path'] for p, d in controls.items()}
        physical.update({p: d['file']['path'] for p, d in payloads.items()})
        reverse = {p: address for address, p in physical.items()}
        if len(reverse) != len(physical):
            raise ValueError('Ambiguous physical owner in pinned read view.')
        children = {}
        for address in physical:
            parts = address.split('/')
            for length in range(len(parts)):
                children.setdefault('/'.join(parts[:length]), set()).add(parts[length])
        return {'snapshot': snapshot, 'controls': controls, 'physical': physical,
                'reverse': reverse, 'children': children}

    @contextmanager
    def operation(self):
        if self._operation.get() is not None:
            yield
            return
        session = self._prepare_operation()
        token = self._operation.set(session)
        try:
            yield
            # Core legacy readers tolerate malformed JSON. Broken immutable
            # storage must instead fail the whole read, not silently hide takes.
            session['snapshot'].verify()
        finally:
            self._operation.reset(token)

    @asynccontextmanager
    async def async_operation(self):
        """Same pinned checks, with disk work off the HTTP event loop.

        Bind/reset ContextVars in the request task itself. Thumbnail tasks and
        streamed requests must keep using their original asyncio event loop.
        """
        if self._operation.get() is not None:
            yield
            return
        session = await asyncio.to_thread(self._prepare_operation)
        token = self._operation.set(session)
        try:
            yield
            await asyncio.to_thread(session['snapshot'].verify)
        finally:
            self._operation.reset(token)

    def _session(self):
        session = self._operation.get()
        if session is None:
            raise ValueError('Combined reads require one pinned operation.')
        return session

    def address(self, value):
        path = Path(value)
        if path.is_absolute():
            try:
                address = path.relative_to(self.project).as_posix()
            except ValueError:
                raise ValueError('Read address is outside the pinned project.') from None
        else:
            canonical = resolver.artifact_address(str(value))
            prefix = 'h3_chains/'+self.project.name+'/'
            if not canonical.startswith(prefix):
                raise ValueError('Read address belongs to another output project.')
            address = canonical[len(prefix):]
        if address == '.':
            return ''
        address = resolver.artifact_address(address)
        session = self._session()
        if address in session['reverse']:
            return session['reverse'][address]
        if address.startswith(('project/', 'media/', 'exports/', '__storage__/', '__migration__/')):
            raise ValueError('Unaccepted storage-internal path is not a logical file.')
        return address

    def logical_output(self, value):
        return 'h3_chains/'+self.project.name+'/'+self.address(value)

    def read(self, value):
        return state._decode(self._control_bytes(self.address(value)))

    def _control_bytes(self, address):
        self._track(address)
        session = self._session()
        if address not in session['controls']:
            raise FileNotFoundError('Missing accepted control document: '+address)
        return session['snapshot'].read(address)

    def read_workflow_archive(self, value, role):
        """Decode verified ComfyUI archives, not H3 authority/settings.

        ComfyUI cache and widget snapshots can legitimately contain NaN or
        Infinity. Preserve those opaque values only in the two archive roles;
        Plans and other controls must still pass the strict authority decoder.
        Read the hashed snapshot bytes directly, never reopen a physical path.
        """
        if role not in ('workflow', 'api_prompt'):
            raise ValueError('Not a ComfyUI workflow archive role.')
        address = self.address(value)
        if not re.fullmatch(
                r'(?:(?:branches|recovery_archives)/[0-9a-f]{32}/)?'+role+r'\.json', address):
            raise ValueError('Address does not match the ComfyUI workflow archive role.')
        document = json.loads(self._control_bytes(address))
        if not isinstance(document, dict):
            raise ValueError('ComfyUI workflow archive must be a JSON object.')
        return document

    def path(self, value):
        """Read-only physical path, preserving genuine missing-artifact reports.

        An unindexed path is NEVER adopted from a leftover legacy file. Missing
        identities can name an absent path so graph readers report broken media.
        """
        address = self.address(value)
        self._track(address)
        session = self._session()
        if address in session['controls']:
            session['snapshot'].read(address)  # verify before exposing a read path
        physical = session['physical'].get(address)
        if physical is not None:
            return resolver.confined(self.project, physical)
        missing = resolver.confined(self.project, address)
        if missing.exists():
            raise ValueError('Unindexed legacy file cannot satisfy a combined-store read.')
        return missing

    def names(self, directory):
        return sorted(self._session()['children'].get(self.address(directory), ()))

    def working_directory(self, run):
        if str(run) != self.project.name:
            raise ValueError('Read view belongs to a different project.')
        selected = branch_id(current_branch(run))
        if selected == 'main':
            return str(self.project)
        path = self.project/'branches'/selected
        self.read(path/'branch.json')
        return str(path)

    def validate_run(self, run):
        self._session()
        if str(run) != self.project.name:
            raise ValueError('Read view belongs to a different project.')

    def recovery_asset(self, descriptor):
        """Resolve an exact saved Source Timeline file from accepted mirrors.

        This is a read-only content match, never a basename search, import, or
        input-folder rewrite. Uncatalogued external sources remain external.
        The saved timeline's fingerprint and the catalogue must both agree with
        the accepted payload bytes before an old machine path is replaced.
        """
        session = self._session()
        digest = descriptor.get('file_sha256') if isinstance(descriptor, dict) else None
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            return None
        catalogue = 'project_assets/catalog.json'
        if catalogue not in session['controls']:
            return None
        document = self.read(self.project/catalogue)
        if (not isinstance(document, dict) or document.get('format') != 'h3_project_assets_v1'
                or document.get('project') != self.project.name
                or not isinstance(document.get('assets'), list)):
            raise ValueError('Invalid accepted project asset catalogue.')
        for asset in document['assets']:
            if not isinstance(asset, dict):
                raise ValueError('Invalid accepted project asset record.')
            if asset.get('sha256') != digest:
                continue
            relative = resolver.artifact_address(asset.get('relative_path'))
            if relative.split('/')[0] not in ('audio', 'videos', 'images'):
                raise ValueError('Invalid project recovery asset address.')
            address = 'project_assets/'+relative
            self._track(address)
            key = project.payload_key(address)
            if key not in session['snapshot']._validated_root()['documents']:
                raise FileNotFoundError('Accepted recovery asset payload is missing: '+address)
            record = project._indexed_record(session['snapshot'], key)
            if record['file']['sha256'] != digest or record['file']['size'] != asset.get('size'):
                raise state.StateConflict('Project recovery asset differs from its accepted catalogue.')
            return self.store.payload_path(session['snapshot'], address, verify=True)
        return None
