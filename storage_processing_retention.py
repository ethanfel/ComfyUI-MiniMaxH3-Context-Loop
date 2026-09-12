"""Typed processing/PNG cleanup using logical files in one accepted snapshot.

Reuse the existing processing independence and PNG-owner rules without giving
their legacy deletion loop physical immutable paths. This view has no writer;
RuntimeRetention owns the separate permission, transaction and undo boundary.
"""
import re

from . import storage_state as state, storage_project as project
from .processing_checkpoint_delete import ProcessingCheckpointManager, REVISION, POINTER, MANIFESTS
from .checkpoint_variants import validate_processing_lineage
from .storage_project_reads import ProjectReadView
from .png_export_ownership import FORMAT as CATALOG_FORMAT, RUNTIME_CATALOG
from .png_video_export import FORMAT as PNG_FORMAT
from .artifact_paths import artifact_address

PROFILE = re.compile(r'(?P<profile>(?:branches/[0-9a-f]{32}/)?(?:chapters/[^/]+/)?upscaled/[^/]+)/(?P<file>.+)')


class ProcessingSnapshot(ProcessingCheckpointManager):
    def __init__(self, runtime, base):
        super().__init__(runtime.output)
        self.runtime, self.base = runtime, base
        # Snapshot.state is a defensive deep copy of the whole catalogue. Take
        # one private copy for this operation, never one per PNG/frame lookup.
        self.descriptors = base.state['documents']
        self.view = ProjectReadView(runtime.store, base=base)

    def _path(self, address):
        # Return a logical Path for ownership/identity comparisons only.
        # All reads/stats explicitly resolve through this pinned view.
        return self.view.project/self.view.address(artifact_address(address))

    def _address(self, path):
        return self.view.logical_output(path)

    def _read(self, path):
        value = self.view.read(path)
        if not isinstance(value, dict):
            raise ValueError('Processing control must be an object: '+self._address(path))
        return value

    def _exists(self, path):
        return self.view.path(path).exists()

    def _is_file(self, path):
        return self.view.path(path).is_file()

    def _stat(self, path):
        return self.view.path(path).stat()

    def _documents(self, run):
        self.view.validate_run(run)
        docs = {}
        for address in sorted(self.descriptors):
            match = PROFILE.fullmatch(address)
            if not match:
                continue
            filename, profile = match['file'], self.view.project/match['profile']
            checkpoint = filename.startswith('checkpoints/') and filename.endswith('.json')
            manifest = filename in ('upscale_manifest.json', 'latest_manifest.json') or (
                filename.startswith('partial/through_clip_') and filename.endswith('.manifest.json'))
            if not (checkpoint or manifest):
                continue
            path = self.view.project/address
            value = self._read(path)
            if (value.get('run_name') != run or value.get('profile') != profile.name
                    or value.get('format') not in MANIFESTS | {'h3_chain_upscale_segment_v1'}):
                raise ValueError('Cannot verify processing metadata: '+address)
            if checkpoint:
                revision_file = REVISION.fullmatch(path.name)
                segment = value.get('segment')
                if (value['format'] != 'h3_chain_upscale_segment_v1' or not isinstance(segment, dict)
                        or not (revision_file or POINTER.fullmatch(path.name))):
                    raise ValueError('Invalid processing checkpoint: '+address)
                scene, revision = segment.get('index'), segment.get('revision')
                if (type(scene) is not int or scene < 1 or not re.fullmatch(r'[0-9a-f]{32}', str(revision))
                        or path.name != ('clip_%04d.%s.json' % (scene, revision) if revision_file else 'clip_%04d.json' % scene)
                        or self._path(segment.get('revision_metadata')) != profile/'checkpoints'/('clip_%04d.%s.json' % (scene, revision))):
                    raise ValueError('Processing revision identity mismatch: '+address)
                if 'processing_lineage' in value:
                    validate_processing_lineage(value['processing_lineage'])
            elif not isinstance(value.get('segments'), list):
                raise ValueError('Processing manifest has no segment list: '+address)
            docs[path] = value
        return docs

    def exports(self):
        directories = set()
        for address in ('png_exports.json', RUNTIME_CATALOG):
            if address not in self.descriptors:
                continue
            record = self._read(self.view.project/address)
            if (record.get('format') != CATALOG_FORMAT or record.get('run_name') != self.runtime.run
                    or not isinstance(record.get('directories'), list)):
                raise ValueError('Invalid accepted PNG ownership catalogue.')
            directories.update(self._path(item) for item in record['directories'])
        # Indexed legacy defaults may predate the ownership catalogue. Inspect
        # accepted names only; never adopt loose files by walking the disk.
        for address in self.descriptors:
            match = PROFILE.fullmatch(address)
            if match and re.fullmatch(r'frames/[^/]+/export\.json', match['file']):
                directories.add((self.view.project/address).parent)
        records = {}
        for directory in sorted(directories):
            path = directory/'export.json'
            if not self._exists(path):
                continue
            record = self._read(path)
            if record.get('format') != PNG_FORMAT or record.get('settings', {}).get('run_name') != self.runtime.run:
                raise ValueError('Cannot verify indexed PNG export: '+self._address(path))
            records[path] = record
        return records

    def preview(self, address):
        with self.view.operation():
            address = self._address(self._path(address))
            logical = self.view.address(address)
            match = PROFILE.fullmatch(logical)
            if not match or not REVISION.fullmatch(match['file'].removeprefix('checkpoints/')):
                raise ValueError('Processing retirement requires exact immutable take metadata.')
            branch = logical.split('/')[1] if logical.startswith('branches/') else 'main'
            if branch != self.runtime.selected:
                raise ValueError('Processing retirement belongs to another working branch.')
            result = self._preview(self.runtime.run, address, self.exports())
            updates = {}
            for full_address, value in result['_png_updates'].items():
                target = self.view.address(full_address)
                original = self.base.read(target)
                if state._decode(original) == value:
                    continue
                updates[target] = state._encode(value)
            # Disk stat hints are informative, but the transaction witness is
            # bound to accepted control/index descriptors and verified bytes.
            files, exceptions = [], {}
            for item in result['files']:
                address = self.view.address(item['path'])
                key = address if address in self.descriptors else project.payload_key(address)
                if key not in self.descriptors:
                    if item['exists']:
                        raise ValueError('Unaccepted file cannot be retired: '+address)
                    continue  # An absent, never-indexed optional file has no catalogue entry.
                files.append(address)
                if key != address:
                    # Ownership has already been proven by exact take/PNG
                    # contracts. Only PNG pixels may be deliberately edited;
                    # missing owned media is removable without inventing bytes.
                    exceptions[address] = ['missing']
                    if item['label'] == 'PNG frame for deleted upscale (including edited pixels)':
                        exceptions[address].append('edited')
            return result, files, updates, exceptions
