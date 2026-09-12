"""Recoverable ordinary-output copies of already accepted assembly files.

This is deliberately a second publication, not a cross-filesystem transaction.
An exclusive basename claim coordinates H3 writers; every actual file move is
also no-replace, so unrelated writers cannot be overwritten. Video is published
last, after its subtitle. Failed attempts/claims stay available for recovery.
"""
import hashlib
import os
from pathlib import Path

from . import storage_state as state
from .processing_persistence import publish_new_file, sync_directory
from .storage_layout import OrganizedStorageLayout
from .storage_project import _hash_file
from .storage_resolver import confined

FORMAT = 'h3_storage_output_copy_v1'
SUFFIXES = {'subtitles': '.srt', 'video': '.mp4'}


def validate_folder(output, folder, budget=240):
    """Only ordinary output space; never another chain or internal copy jobs."""
    if not isinstance(folder, str) or '\\' in folder:
        raise ValueError('Output copy needs a canonical relative folder.')
    if folder:
        if folder.split('/')[0].casefold() in ('h3_chains', '.h3_export_copies'):
            raise ValueError('Output copy cannot target chain storage or copy journals.')
        confined(output, folder)
    OrganizedStorageLayout(str(output), budget).check_budget(
        (folder+'/' if folder else '')+'.h3copy-'+'f'*32+'-10000-subtitles-10000.part')


class OutputCopy:
    def __init__(self, workspace, saved, snapshot):
        self.work, self.runtime = workspace, workspace.runtime
        self.saved, self.snapshot = saved, snapshot
        self.operation, self.budget = workspace.operation, workspace.budget
        self.output, self.project, self.job = self.runtime.output, self.runtime.project, workspace.job
        self.folder = workspace.request['copy_subfolder']
        validate_folder(self.output, self.folder, self.budget)
        previous = self._read('copy-request.json')
        self.readable = previous is None or previous.get('naming') == 'readable_v1'
        self.basename = workspace.final_name if self.readable else Path(saved['logical']['video']).stem
        self.files = {}
        for role in SUFFIXES:
            if role in saved['files']:
                path = self.runtime.store.payload_path(snapshot, saved['logical'][role], verify=True)
                self.files[role] = dict(source=path.relative_to(self.project).as_posix(),
                    sha256=saved['files'][role]['sha256'], size=saved['files'][role]['size'])
        self.identity = dict(format=FORMAT, operation=self.operation, project=self.project.name,
            assembly_request_sha256=workspace.request_hash, folder=self.folder,
            basename=self.basename, files=self.files)
        if self.readable:
            self.identity['naming'] = 'readable_v1'
        self.request_hash = state._hash(state._encode(self.identity))
        self._write('copy-request.json', self.identity)

    def _write(self, name, value):
        with self.runtime.exports.guard(self.work.proof):
            state._immutable(self.project, self.job+'/'+name, state._encode(value), self.budget)

    def _read(self, name):
        path = confined(self.project, self.job+'/'+name)
        return state._decode(state._read_bytes(path)) if path.exists() else None

    def _notify(self, stage):
        if self.runtime.exports.after_stage:
            self.runtime.exports.after_stage('copy:'+stage)

    def _paths(self, ordinal):
        suffix = ('_'+str(ordinal+1) if self.readable else '_%03d' % ordinal) if ordinal else ''
        stem = self.basename+suffix
        prefix = self.folder+'/' if self.folder else ''
        names = {role:prefix+stem+SUFFIXES[role] for role in self.files}
        policy = OrganizedStorageLayout(str(self.output), self.budget)
        for value in names.values():
            policy.check_budget(value)
            confined(self.output, value)
        # Case-fold to coordinate Windows and case-insensitive shares too.
        claim = '.h3_export_copies/claims/'+state._hash((prefix+stem).casefold().encode())+'.json'
        policy.check_atomic_json_budget(claim)
        return names, claim

    def _validate(self, plan):
        if (not isinstance(plan,dict) or plan.get('request_sha256') != self.request_hash
                or type(plan.get('ordinal')) is not int or not 0 <= plan['ordinal'] <= 10000):
            raise state.StateConflict('Output copy plan differs from its accepted assembly.')
        paths, claim = self._paths(plan['ordinal'])
        expected = dict(request_sha256=self.request_hash, ordinal=plan['ordinal'], paths=paths, claim=claim)
        if plan != expected:
            raise state.StateConflict('Output copy destinations changed.')
        claim_path = confined(self.output, claim)
        if state._decode(state._read_bytes(claim_path)) != dict(self.identity, destinations=paths):
            raise state.StateConflict('Output copy basename is owned by another operation.')

    def _claim(self, ordinal):
        paths, claim = self._paths(ordinal)
        expected = dict(self.identity, destinations=paths)
        target = confined(self.output, claim)
        if target.exists():
            if state._decode(state._read_bytes(target)) != expected:
                return None
        else:
            # Even an orphan subtitle occupies this basename as a whole.
            video_stem = paths['video'][:-4]
            if any(confined(self.output, video_stem+suffix).exists() for suffix in SUFFIXES.values()):
                return None
            with self.runtime.exports.guard(self.work.proof):
                try:
                    state._immutable(self.output, claim, state._encode(expected), self.budget)
                except (FileExistsError, ValueError) as error:
                    # Another project can claim this output basename before
                    # _immutable's initial read (ValueError), or during its
                    # no-replace move (FileExistsError). Only a verified
                    # competing claim is a collision; don't hide I/O errors.
                    target = confined(self.output, claim)
                    if not target.is_file():
                        raise
                    if state._decode(state._read_bytes(target)) != expected:
                        return None
                    if not isinstance(error, FileExistsError):
                        raise
                    sync_directory(target.parent)
        return dict(request_sha256=self.request_hash, ordinal=ordinal, paths=paths, claim=claim)

    def _matches(self, path, expected):
        digest, signature = _hash_file(path)
        return digest == expected['sha256'] and signature[2] == expected['size']

    def _copy_file(self, role, plan):
        expected = self.files[role]
        source = confined(self.project, expected['source'])
        destination = confined(self.output, plan['paths'][role])
        if not self._matches(source, expected):
            raise state.StateConflict('Accepted assembly source changed before output copy.')
        if destination.exists():
            # An existing exact owned copy recovers an ambiguous rename/fsync.
            return self._matches(destination, expected)
        with self.runtime.exports.guard(self.work.proof):
            state._mkdir(destination.parent, self.output)
        for attempt in range(1,10001):
            name = '.h3copy-%s-%04d-%s-%04d.part' % (self.operation,plan['ordinal'],role,attempt)
            address = (self.folder+'/' if self.folder else '')+name
            OrganizedStorageLayout(str(self.output),self.budget).check_budget(address)
            partial = confined(self.output,address)
            try:
                with self.runtime.exports.guard(self.work.proof):
                    dst = partial.open('xb')
                break
            except FileExistsError:
                continue  # Retain interrupted partials, never truncate them.
        else:
            raise ValueError('Too many interrupted output-copy attempts.')
        with dst, source.open('rb') as src:
            digest = hashlib.sha256()
            count = 0
            for block in iter(lambda:src.read(1024*1024), b''):
                digest.update(block)
                count += len(block)
                dst.write(block)
                self.work.chain._png_export_check_interrupted()
            dst.flush()
            os.fsync(dst.fileno())
        if digest.hexdigest() != expected['sha256'] or count != expected['size'] or not self._matches(source,expected):
            raise state.StateConflict('Assembly source changed while copying; partial retained.')
        with self.runtime.exports.guard(self.work.proof):
            # Recheck links/junctions just before the no-replace publication.
            confined(self.output,address)
            confined(self.output,plan['paths'][role])
            try:
                publish_new_file(partial,destination)
            except FileExistsError:
                return self._matches(destination,expected)
            sync_directory(destination.parent)
        self._notify(role)
        return True

    def _verify_complete(self, plan):
        self._validate(plan)
        for role, address in plan['paths'].items():
            if not self._matches(confined(self.output,address),self.files[role]):
                raise state.StateConflict('Completed output copy was changed; existing files retained.')

    def publish(self):
        completed = self._read('copy-complete.json')
        if completed is not None:
            self._verify_complete(completed)
            return {role:str(confined(self.output,address)) for role,address in completed['paths'].items()}
        for ordinal in range(10001):
            name = 'copy-plan-%04d.json' % ordinal
            if self._read('copy-collision-%04d.json' % ordinal) is not None:
                continue
            plan = self._read(name)
            if plan is None:
                plan = self._claim(ordinal)
                if plan is None:
                    continue
                self._write(name,plan)
            self._validate(plan)
            self._notify('prepared')
            # SRT first, MP4 last; a visible completed video has its subtitle.
            if not all(self._copy_file(role,plan) for role in self.files):
                self._write('copy-collision-%04d.json' % ordinal,dict(request_sha256=self.request_hash))
                continue
            self._verify_complete(plan)
            self._write('copy-complete.json',plan)
            self._notify('complete')
            return {role:str(confined(self.output,address)) for role,address in plan['paths'].items()}
        raise ValueError('No unused output-copy basename is available; accepted assembly retained.')
