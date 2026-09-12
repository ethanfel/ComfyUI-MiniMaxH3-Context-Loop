"""Deferred Review batches published with their exact stopping transition.

The old pending_reviews identity remains readable through the accepted index;
its bytes live in organized immutable controls. A pending record is inventory,
not permission to activate a take, change a Plan or delete candidates.
"""
import copy
from datetime import datetime
from pathlib import Path
import re

from . import storage_state as control
from .storage_review_selection import verify_media
from .storage_project import payload_catalog, payload_key
from .branch_scope import current_branch

FORMAT = 'h3_deferred_review_v1'


def _check(runtime):
    runtime.check()
    if current_branch(runtime.run) != runtime.selected:
        raise ValueError('Deferred Review request belongs to another runtime branch.')


def _item(output, path):
    relative = Path(path).relative_to(output)
    return dict(filename=relative.name, subfolder=relative.parent.as_posix(), type='output')


def normalize_previews(runtime, document):
    """Archive logical preview identities so reverse recovery stays portable."""
    _check(runtime)
    document = copy.deepcopy(document)
    physical = {str(runtime.project/value['file']['path']): key
                for key, value in payload_catalog(runtime.accepted).items()}
    for candidate in document['candidates']:
        video = candidate['video']
        path = str(runtime.output / video['subfolder'] / video['filename'])
        if path not in physical:
            raise control.StateConflict('Deferred Review preview has not been accepted.')
        candidate['video'] = _item(runtime.output, runtime.project/physical[path])
    document['public']['video'] = document['candidates'][-1]['video']
    # Only preview addresses change; exact public seed/prompt and batch ordering
    # remain subject to the same publication validator.
    for public, candidate in zip(document['public']['candidates'], document['candidates']):
        public['video'] = candidate['video']
    return document


def address(branch, token):
    prefix = '' if branch == 'main' else 'branches/'+control._token(branch)+'/'
    return prefix+'pending_reviews/'+control._token(token)+'.json'


def validate_identity(document, run, branch, token):
    if (not isinstance(document, dict) or document.get('format') != FORMAT
            or document.get('version') != 1 or document.get('run_name') != run
            or document.get('token') != token
            or not isinstance(document.get('plan'), dict)
            or document['plan'].get('run_name') != run
            or document['plan'].get('_branch_id', 'main') != branch
            or not isinstance(document.get('candidates'), list)
            or not document['candidates'] or not isinstance(document.get('public'), dict)
            or type(document.get('scene')) is not int or document['scene'] < 1):
        raise ValueError('Pending Review identity differs from its project, branch or token.')
    public = document['public']
    if (public.get('token') != token or public.get('run_name') != run
            or public.get('_branch_id', 'main') != branch
            or public.get('clip_index') != document['scene']):
        raise ValueError('Pending Review display identity differs from its saved batch.')
    return document


def validate_publication(execution, document, base):
    """Rebuild provenance on prepared-result retry; a checksum is not authority."""
    runtime, chain = execution.runtime, execution.chain
    _check(runtime)
    validate_identity(document, runtime.run, runtime.selected, execution.operation)
    if not chain._deferred_review_enabled(execution.inputs.get('pending_review')):
        raise ValueError('This Review invocation did not request a deferred batch.')
    target = chain._review_candidate_target(execution.inputs['candidate_count'])
    state = execution.state
    expected = [item['segment'] for item in chain._review_batch_candidates(state, state['index'], target)]
    expected.append(execution.segment)
    if (len(expected) != target or len(document['candidates']) != target
            or document['scene'] != state['index']
            or document['plan'] != chain._archivable_plan(state['plan'])):
        raise control.StateConflict('Deferred Review changed its exact Plan or completed candidate batch.')
    datetime.fromisoformat(document['created_at'].replace('Z', '+00:00'))
    scopes = {'branch:'+runtime.selected}
    descriptors = base.state['documents']
    payloads = payload_catalog(base)
    for item, source in zip(document['candidates'], expected):
        if (not isinstance(item, dict)
                or item.get('segment') != chain._public_segment(source)):
            raise control.StateConflict('Deferred Review candidate differs from its saved input.')
        revision = control._token(source['revision'])
        key = 'checkpoints/clip_%04d.%s.json' % (state['index'], revision)
        metadata = control._decode(base.read(key))
        saved = metadata.get('segment')
        if (not isinstance(saved, dict) or saved.get('revision') != revision
                or saved.get('index') != state['index']
                or any(value != source[key] for key, value in chain._public_segment(saved).items()
                       if key in source)):
            raise control.StateConflict('Deferred Review candidate disagrees with immutable metadata.')
        verify_media(runtime, base, saved)
        scopes.add(descriptors[key]['scope'])
        video = item.get('video')
        if (not isinstance(video, dict) or set(video) != {'filename', 'subfolder', 'type'}
                or video['type'] != 'output' or Path(video['filename']).name != video['filename']):
            raise ValueError('Deferred Review preview is not an accepted output item.')
        visible = runtime.output / video['subfolder'] / video['filename']
        media_address = visible.relative_to(runtime.project).as_posix()
        if media_address not in payloads:
            raise control.StateConflict('Deferred Review preview is not accepted at its publication root.')
        runtime.store.payload_path(base, media_address, verify=True)
        scopes.add(descriptors[payload_key(media_address)]['scope'])
        source_address = runtime.reader.address(saved['segment'])
        if media_address != source_address:
            prefix = '' if runtime.selected == 'main' else 'branches/'+runtime.selected+'/'
            match = re.fullmatch(re.escape(prefix)+r'reviews/([0-9a-f]{32})\.mp4', media_address)
            if match is None:
                raise ValueError('Deferred Review preview belongs to another candidate or branch.')
            preview_key = 'jobs/'+match[1]+'/review-preview.json'
            preview = control._decode(base.read(preview_key))
            if preview.get('revision') != revision or preview.get('source') != source_address:
                raise control.StateConflict('Deferred Review preview has different saved provenance.')
            scopes.add(descriptors[preview_key]['scope'])
    public = document['public']
    shot = state['plan']['shots'][state['index']-1]
    required = dict(deferred_review=True, pending_decision=True,
        candidate_batch_active=False, candidate_generation_complete=True,
        candidate_count=target, candidate_index=target, deadline=None, timeout_seconds=0.0,
        clip_count=len(state['plan']['shots']), end_clip=int(state.get('range_end', len(state['plan']['shots']))),
        seed=str(shot['seed']), raw_frames=int(shot['raw_frames']),
        scene_prompt=shot.get('scene_prompt', shot['prompt']),
        candidates=chain._review_public_candidates(document['candidates']),
        video=document['candidates'][-1]['video'], has_audio=document['candidates'][-1]['has_audio'])
    if any(public.get(key) != value for key, value in required.items()):
        raise control.StateConflict('Deferred Review display changed its candidate identity, prompt or seed.')
    return scopes


def read(runtime, token):
    _check(runtime)
    token = control._token(token)
    key = address(runtime.selected, token)
    if key not in runtime.base.state['documents']:
        raise FileNotFoundError('Pending H3 review was not found: '+token[:8])
    try:
        value = control._decode(runtime.base.read(key))
    except FileNotFoundError as exc:
        raise control.StateConflict('Accepted pending Review bytes are missing: '+key) from exc
    value = copy.deepcopy(validate_identity(value, runtime.run, runtime.selected, token))
    from .storage_deferred_finalization import accepted_projection
    value = accepted_projection(runtime, value)
    finalized = value.get('_finalization', {})
    if finalized.get('status') == 'complete':
        return value
    # Stored logical identities work in a recovered ordinary folder. In the
    # combined runtime the browser needs the verified physical accepted file.
    for candidate in value['candidates']:
        if finalized and candidate['segment']['revision'] not in finalized['kept_candidate_revisions']:
            # Rejected takes may already be quarantined during an interrupted
            # cleanup. Keep the original batch identity, but do not resolve a
            # retired preview or make it selectable again.
            candidate['video'] = None
            continue
        video = candidate.get('video')
        if not isinstance(video, dict):
            continue
        path = runtime.output / video['subfolder'] / video['filename']
        resolved = runtime.reader.path(path)
        candidate['video'] = _item(runtime.output, resolved)
    for public, candidate in zip(value['public'].get('candidates', []), value['candidates']):
        public['video'] = candidate.get('video')
    value['public']['video'] = value['candidates'][-1].get('video')
    return value


def listing(runtime):
    _check(runtime)
    prefix = address(runtime.selected, '0'*32).rsplit('/', 1)[0]+'/'
    result = []
    for key in sorted(runtime.base.state['documents']):
        if not key.startswith(prefix):
            continue
        tail = key[len(prefix):]
        if '/' in tail or not tail.endswith('.json'):
            continue
        # Corrupt accepted records must be visible as an error, never hidden as
        # an empty list that appears to have lost the user's candidate batch.
        value = read(runtime, tail[:-5])
        if value.get('_finalization', {}).get('status') == 'complete':
            continue
        public = dict(value['public'], created_at=value['created_at'])
        result.append(public)
    return sorted(result, key=lambda item: (item['clip_index'], item['created_at']), reverse=True)
