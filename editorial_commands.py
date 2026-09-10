"""Inspect, review and conditionally save native saved-sequence edits."""
import copy
import hashlib
import json
from pathlib import Path

from .branch_scope import branch_id, branch_scope

VERSION = 1


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{label} must be an integer between {minimum} and {maximum}.')
    return value


def state(chain, run, selected):
    store = chain.WorkingBranches(chain._output_root(), run)
    if not store.root.is_dir():
        raise ValueError('The saved project is unavailable.')
    store._load_record(selected)
    pointers = store._pointers(selected)
    segments = []
    for index, metadata in sorted(pointers.items()):
        segment = metadata.get('segment')
        if not isinstance(segment, dict) or segment.get('index') != index or not segment.get('id'):
            raise ValueError(f'Scene {index} has invalid saved checkpoint identity.')
        segments.append(segment)
    path = Path(chain._run_editorial_path(run))
    editorial = (chain._normalize_run_editorial(chain._read_json(str(path)), run)
                 if path.exists() else chain._load_run_editorial(run))
    catalog = chain.ProjectAssetStore(chain._input_root(), chain._output_root()).load(run, repair=False)
    stamp = digest([run, selected, path.exists(), editorial, pointers,
                    catalog.get('revision', ''), catalog.get('assets', [])])
    return {'editorial': editorial, 'segments': segments, 'catalog': catalog, 'stamp': stamp}


def safe_outs(chain, segment):
    raw, delivered = int(segment.get('raw_frames', 0)), int(segment.get('delivered_frames', 0))
    if not 1 <= delivered <= raw <= chain.MAX_H3_FRAMES:
        raise ValueError('The saved checkpoint has invalid raw/delivered duration.')
    return [out for out in range(1, delivered + 1)
            if out == delivered or (out * int(chain.AUDIO_HZ) % chain.FPS == 0
                and chain._h3_prefix_frame_boundary_step(raw - delivered + out) is not None)]


def describe(chain, run, source, document):
    segments = source['segments']
    adjusted = {int(s['index']): chain._editorial_trimmed_segment(s, document) for s in segments}
    stale = chain._editorial_stale_dependencies(adjusted)
    # A diagnostic can show the proposed layout and affected continuations.
    # Actual native assembly retains its default dependency enforcement.
    _, records, frames = chain._editorial_timeline_records(
        run, segments, document, validate_dependencies=False)
    cues = chain._editorial_subtitle_cues(run, document, frames, catalog=source['catalog'])
    return {'frames': frames, 'fps': chain.FPS, 'subtitle_count': len(cues),
            'cues': cues, 'stale_scenes': [{'scene': scene, 'reasons': reasons} for scene, reasons in stale.items()],
            'records': [{k: v for k, v in record.items() if k != 'segment'} for record in records]}


def inspect(chain, run, selected, source):
    document = source['editorial']
    scenes = []
    for segment in source['segments']:
        index, identity = int(segment['index']), segment['id']
        trim = next((v for v in document.get('trims', []) if v['scene_id'] == identity), None)
        placement = next((v for v in document.get('placements', []) if v['scene_id'] == identity), None)
        scenes.append({'scene': index, 'scene_id': identity,
                       'revision': chain.checkpoint_revision_token(index, segment),
                       'raw_frames': segment['raw_frames'], 'delivered_frames': segment['delivered_frames'],
                       'out_frame': trim['out_frame'] if trim else segment['delivered_frames'],
                       'start_frame': placement['start_frame'] if placement else None,
                       'locked': identity in document.get('locked_scene_ids', []),
                       'safe_out_frames': safe_outs(chain, segment)})
    try:
        timeline = describe(chain, run, source, document)
    except ValueError as exc:
        timeline = {'error': str(exc)}
    return {'version': VERSION, 'run_name': run, 'branch_id': selected, 'stamp': source['stamp'],
            'editorial_revision': document.get('revision', ''), 'scenes': scenes, 'timeline': timeline,
            'subtitles': copy.deepcopy(document.get('subtitles', {})),
            'subtitle_assets': [{'id': a['id'], 'tag': a.get('tag', a['id']),
                                 'timed': bool(chain._parse_timed_lyrics(a.get('lyrics', '')))}
                                for a in source['catalog'].get('assets', []) if a.get('kind') == 'audio']}


def proposal(chain, run, source, patch):
    if not isinstance(patch, dict) or len(patch) != 1 or next(iter(patch)) not in ('scene', 'subtitles'):
        raise ValueError('Choose one scene edit or subtitle settings edit.')
    document = copy.deepcopy(source['editorial'])
    edit = patch.get('scene')
    if edit is not None:
        allowed = {'scene', 'scene_id', 'revision', 'out_frame', 'start_frame', 'locked'}
        if not isinstance(edit, dict) or set(edit) - allowed or not set(edit) & {'out_frame', 'start_frame', 'locked'}:
            raise ValueError('Unsupported saved scene edit.')
        scene = integer(edit.get('scene'), 1, chain.MAX_SHOTS, 'Scene')
        segment = next((s for s in source['segments'] if s['index'] == scene), None)
        if (segment is None or segment['id'] != edit.get('scene_id')
                or chain.checkpoint_revision_token(scene, segment) != edit.get('revision')):
            raise chain.EditorialConflictError('The saved scene changed. Inspect this branch again.')
        identity = segment['id']
        if identity in document.get('locked_scene_ids', []) and set(edit) & {'out_frame', 'start_frame'}:
            raise ValueError('Unlock this saved scene before changing its trim or placement.')
        order = document.setdefault('scene_order', [])
        if not any(row['scene'] == scene and row['scene_id'] == identity for row in order):
            if any(row['scene'] == scene or row['scene_id'] == identity for row in order):
                raise ValueError('The saved editorial scene order differs from the checkpoint. Refresh Plan Studio.')
            order.append({'scene': scene, 'scene_id': identity})
        if 'out_frame' in edit:
            out = edit['out_frame']
            if out is not None:
                integer(out, 1, int(segment['delivered_frames']), 'Trim end')
                if out not in safe_outs(chain, segment):
                    raise ValueError('Trim end must use a native video/audio boundary.')
            document['trims'] = [v for v in document.get('trims', []) if v['scene_id'] != identity]
            if out is not None and out < int(segment['delivered_frames']):
                document['trims'].append({'scene': scene, 'scene_id': identity, 'out_frame': out})
        if 'start_frame' in edit:
            start = edit['start_frame']
            if start is not None:
                integer(start, 0, 864000, 'Placement')
            document['placements'] = [v for v in document.get('placements', []) if v['scene_id'] != identity]
            if start is not None:
                document['placements'].append({'scene': scene, 'scene_id': identity, 'start_frame': start})
        if 'locked' in edit:
            if type(edit['locked']) is not bool:
                raise ValueError('Scene lock must be true or false.')
            document['locked_scene_ids'] = [v for v in document.get('locked_scene_ids', []) if v != identity]
            if edit['locked']:
                document['locked_scene_ids'].append(identity)
    else:
        subtitles = patch['subtitles']
        if not isinstance(subtitles, dict) or not subtitles or set(subtitles) - {'mode', 'asset_id', 'offset_seconds'}:
            raise ValueError('Unsupported subtitle settings.')
        document['subtitles'] = {**document.get('subtitles', {}), **subtitles}
    normalized = chain._save_run_editorial_document_unlocked(document, persist=False)
    return normalized, describe(chain, run, source, normalized)


def command(chain, body, ownership=None):
    if not isinstance(body, dict):
        raise ValueError('Saved sequence commands require a JSON object.')
    run = chain._strict_run_name(body.get('run_name'))
    selected = branch_id(body.get('branch_id', 'main'))
    action = body.get('action')
    if action not in ('inspect', 'preview', 'apply'):
        raise ValueError('Unknown saved sequence command.')
    with branch_scope(run, selected):
        if action == 'apply':
            with chain.checkpoint_run_lock(chain._output_root(), run):
                source = state(chain, run, selected)
                if body.get('stamp') != source['stamp']:
                    raise chain.EditorialConflictError('The saved cut, checkpoints or assets changed. Review the edit again.')
                document, timeline = proposal(chain, run, source, body.get('patch'))
                token = digest([source['stamp'], document])
                if body.get('preview_token') != token:
                    raise chain.EditorialConflictError('The reviewed edit changed. Preview it again.')
                document['base_revision'] = source['editorial'].get('revision', '')
                saved = chain._save_run_editorial_document(document, ownership)
                return {'version': VERSION, 'run_name': run, 'branch_id': selected,
                        'editorial': saved, 'timeline': timeline}
        source = state(chain, run, selected)
        if action == 'inspect':
            return inspect(chain, run, selected, source)
        if body.get('stamp') != source['stamp']:
            raise chain.EditorialConflictError('The saved cut, checkpoints or assets changed. Inspect this branch again.')
        document, timeline = proposal(chain, run, source, body.get('patch'))
        return {'version': VERSION, 'run_name': run, 'branch_id': selected, 'stamp': source['stamp'],
                'preview_token': digest([source['stamp'], document]), 'timeline': timeline,
                'patch': copy.deepcopy(body['patch'])}
