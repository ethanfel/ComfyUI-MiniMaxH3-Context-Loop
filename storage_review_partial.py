"""Review Stop's accepted decision followed by an exact, retryable partial export."""
import copy
import uuid

from . import storage_state as control
from .storage_assembly import AssemblyExport
from .storage_runtime import accepted_export_access
from .project_ownership import ProjectOwnershipError

FORMAT = 'h3_review_partial_export_v1'


class PartialStopExport(AssemblyExport):
    def __init__(self, runtime, upscale, manifest, *, review_receipt, scene, requested_audio, **settings):
        self.review_receipt = copy.deepcopy(review_receipt)
        self.scene, self.requested_audio = scene, requested_audio
        operation = uuid.uuid5(uuid.UUID(review_receipt['operation_id']),
                              'partial-stop:'+settings['audio_source']).hex
        super().__init__(runtime, upscale, manifest, operation=operation, **settings)
        self.partial_manifest = self.branch+'partial/through_clip_%04d.manifest.json' % scene
        self.partial_receipt = self.branch+'partial_reviews/'+operation+'.json'

    def _companion_controls(self, saved):
        manifest = {key:value for key,value in saved['manifest'].items()
                    if key not in ('_project_ownership', '_storage_pin')}
        record = dict(format=FORMAT, review_receipt=self.review_receipt,
            scene=self.scene, requested_audio=self.requested_audio,
            audio_source=self.settings['audio_source'], manifest=self.partial_manifest,
            assembly_witness=self.witness, outputs=saved['logical'])
        scope = 'branch:'+self.runtime.selected
        return {
            self.partial_manifest:dict(data=control._encode(manifest), scope=scope,
                                       category='cuts', immutable=False),
            self.partial_receipt:dict(data=control._encode(record), scope='jobs:'+self.operation,
                                      category='jobs', immutable=True),
        }

    def _verify_outputs(self, saved, snapshot):
        super()._verify_outputs(saved, snapshot)
        for key, value in self._companion_controls(saved).items():
            if snapshot.read(key) != value['data']:
                raise control.StateConflict('Partial video, manifest and stopping Review do not match.')


def assemble_partial(execution, selection, review_receipt):
    chain, parent = execution.chain, execution.runtime
    requested = {'checkpointed':'generated', 'source':'source', 'none':'none'}.get(
        execution.inputs['partial_audio_source'])
    if requested is None:
        raise ValueError('Unknown H3 partial audio source.')
    from . import upscale_nodes
    with execution.guard(), accepted_export_access(parent) as bound:
        accepted_state = selection.state if selection is not None else execution.state
        segment = selection.segment if selection is not None else execution.segment
        manifest = chain._partial_manifest(accepted_state, segment)
        sizes = {(size['width'], size['height']) for item in manifest['segments']
                 if (size := chain.saved_resolution(item)) is not None}
        if len(sizes) > 1:
            manifest, _ = chain._chapter_manifest_from_manifest(
                manifest, 0, persist=False, rehearsal_view=bound.reader)
        # Freeze editorial reads at the accepted decision/cleanup root.
        manifest = dict(manifest, editorial=copy.deepcopy(chain._manifest_editorial(manifest)),
            _storage_pin=bound.pin, _branch_id=bound.selected, _project_ownership=execution.proof)
        scene = int(segment['index'])
        def workspace(audio):
            return PartialStopExport(bound, upscale_nodes, manifest,
                review_receipt=review_receipt, scene=scene, requested_audio=requested,
                audio_source=audio, filename='partial_through_clip_%04d' % scene, audio_bitrate=192,
                source_audio=execution.inputs.get('source_audio'), overwrite_existing=True,
                source_timeline=accepted_state.get('source_timeline'),
                boundary_tone_match=chain._partial_boundary_tone_match_mode(manifest))
        silent_operation = uuid.uuid5(uuid.UUID(review_receipt['operation_id']), 'partial-stop:none').hex
        warning = ''
        if requested != 'none' and silent_operation in bound.check().state['operations']:
            # A silent fallback already committed; don't retry the failed audio
            # route or reinterpret this operation as a different output.
            work = workspace('none')
            result = work.accepted()
            warning = 'audio unavailable, so the partial video is silent'
        else:
            work = workspace(requested)
            try:
                work.prepare(chain.MiniMaxH3ChainAssemble())
            except (control.StateConflict, ProjectOwnershipError, OSError):
                raise  # Storage failures are not permission to publish a fallback.
            except Exception:
                chain._png_export_check_interrupted()
                if requested == 'none':
                    raise
                work = workspace('none')
                work.prepare(chain.MiniMaxH3ChainAssemble())
                warning = 'audio unavailable, so the partial video is silent'
            # Publication failures retain the accepted stop and exact prepared
            # output for retry; they must not fall back to another encoding.
            result = work.publish()
        return result, warning
