#!/usr/bin/env python3
"""Native saved-cut snapshot, immutable audio/captions and real FFmpeg delivery."""
import asyncio
import copy
import importlib
import importlib.util
import json
import pathlib
import subprocess
import tempfile
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("delivery_helpers", ROOT / "tests/_checkpoint_revision_unit_test.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
chain = h.chain
delivery = importlib.import_module(chain.__package__ + ".delivery_snapshot")
import torch
from safetensors.torch import save_file


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def reject(call, text):
    try:
        call()
    except (ValueError, OSError) as exc:
        assert text in str(exc), str(exc)
    else:
        raise AssertionError("Expected rejection: " + text)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        h.folder_paths.output_directory = tmp
        run = root / 'h3_chains' / 'delivery_test'
        compat = {'width': 64, 'height': 48, 'fps': 24, 'audio_mode': 'generated_audio',
                  'context_length': 0, 'audio_context_length': 0, 'video_blend_frames': 0}
        def save(scene, token, color, parent=None, alternate=False):
            record, _ = h.write_revision(run, scene, token, 18446744073709551615,
                active=not alternate, predecessor=parent, run_name=run.name, compatibility=compat,
                context_length=0, audio_context_length=0)
            segment = record['segment']
            segment.update(raw_frames=24, delivered_frames=24, sample_rate=8000)
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                f'color=c={color}:s=64x48:r=24:d=1', '-an', '-c:v', 'libx264',
                '-pix_fmt', 'yuv420p', str(root / segment['segment'])], check=True)
            wave = torch.sin(torch.arange(8000) * (2 * torch.pi * 220 / 8000)).reshape(1, 1, -1).repeat(1, 2, 1) * .1
            save_file({'delivered_audio': wave}, str(root / segment['checkpoint']))
            segment['segment_sha256'] = h.digest(root / segment['segment'])
            segment['checkpoint_sha256'] = h.digest(root / segment['checkpoint'])
            if alternate:
                segment.update(take_kind='editorial_alternate', alternate_of_revision='1' * 32)
            (root / segment['revision_metadata']).write_text(json.dumps(record))
            if not alternate:
                (root / segment['metadata']).write_text(json.dumps(record))
            return record
        first = save(1, '1' * 32, 'blue')
        second = save(2, '2' * 32, 'green', first)
        alt = save(1, 'a' * 32, 'red', alternate=True)
        (run / 'plan.json').write_text(json.dumps({'run_name': run.name, 'shots': [{'id': 'scene_1'}, {'id': 'scene_2'}]}))
        editorial_path = run / 'editorial.json'
        editorial_path.write_text(json.dumps({'revision': 'e' * 32,
            'scene_order': [{'scene': 1, 'scene_id': 'scene_1'}, {'scene': 2, 'scene_id': 'scene_2'}],
            'chapters': [{'id': 'first', 'start_scene_id': 'scene_1'},
                         {'id': 'second', 'start_scene_id': 'scene_2'}],
            'subtitles': {'mode': 'preview_srt', 'asset_id': 'lyrics', 'offset_seconds': 0},
            'replacements': [{'scene': 1, 'scene_id': 'scene_1', 'base_revision': '1' * 32,
                              'alternate_revision': 'a' * 32, 'media_mode': 'picture_only'}]}))
        selection = {'run_name': run.name, '_branch_id': 'main', 'output_mode': 'workflow_local',
                     'final_cut_branch_id': 'main', 'lineage': [{'scene': 1, 'revision': '1' * 32},
                                                             {'scene': 2, 'revision': '2' * 32}]}
        revision = chain._load_run_editorial(run.name)['revision']
        body = {'selection': selection, 'editorial_revision': revision}
        catalog = {'assets': [{'id': 'lyrics', 'kind': 'audio', 'tag': 'lyrics',
                               'lyrics': '1\n00:00:00,000 --> 00:00:01,500\nFrozen caption'}]}
        # Branch destination and final-cut source are separate identities.
        branch = chain.WorkingBranches(tmp, run.name).create('main', 'Second cut',
            {'plan_json': json.dumps({'shots': [{'id': 'scene_1'}, {'id': 'scene_2'}]})})
        named = {**body, 'selection': {**selection, '_branch_id': branch['id']}}
        with patch.object(chain.ProjectAssetStore, 'load', return_value=catalog):
            named_snapshot = delivery.prepare(chain, named)
            response = asyncio.run(chain._prepare_saved_delivery(h.JsonRequest(body)))
        assert response.status == 200 and json.loads(response.text)['snapshot_id']
        named_manifest = delivery.load(chain, named_snapshot['snapshot_json'])
        assert named_manifest['_branch_id'] == branch['id']
        assert named_manifest['final_cut_source']['branch_id'] == 'main'
        assert chain.current_branch(run.name) == 'main'
        conflict = asyncio.run(chain._prepare_saved_delivery(h.JsonRequest({**body, 'editorial_revision': 'stale'})))
        assert conflict.status == 409
        before = files(root)
        with patch.object(chain.ProjectAssetStore, 'load', return_value=catalog):
            prepared = delivery.prepare(chain, body)
            chapter = delivery.prepare(chain, {**body, 'selection': {**selection,
                'output_scope': 'chapter', 'scope_start_scene': 2, 'scope_end_scene': 2}})
        assert files(root) == before, 'preparing a delivery changed project files'
        assert chapter['summary']['scene_start'] == 2 and chapter['summary']['frames'] == 24
        assert prepared['summary']['pictures'][0]['revision'] == 'a' * 32
        assert prepared['summary']['frames'] == 48 and prepared['summary']['subtitle_count'] == 1
        assert '18446744073709551615' in prepared['snapshot_json']
        # Real mirror-only catalog: preparation must not repair the missing primary.
        asset_module = importlib.import_module(chain.__package__ + '.project_assets')
        backup = run / 'project_assets/catalog.json'
        backup.parent.mkdir(parents=True)
        backup.write_text(json.dumps({**catalog, 'format': asset_module.PROJECT_ASSET_FORMAT,
                                      'version': 1, 'project': run.name, 'revision': 'saved'}))
        before_recovery = files(root)
        recovered = delivery.prepare(chain, body)
        assert files(root) == before_recovery
        assert not (root / 'h3_projects' / run.name).exists()
        assert recovered['summary']['subtitle_count'] == 1
        reject(lambda: delivery.prepare(chain, {**body, 'editorial_revision': 'wrong'}), 'final cut changed')
        reject(lambda: delivery.prepare(chain, {**body, 'selection': {**selection, 'output_mode': None}}), 'workflow-local')
        reject(lambda: delivery.load(chain, prepared['snapshot_json'].replace('Frozen caption', 'Changed caption')), 'changed or damaged')
        # Later branch edits do not change queued picture or subtitles.
        editorial_path.write_text(json.dumps({'revision': 'f' * 32, 'replacements': [], 'subtitles': {'mode': 'off'}}))
        altered = copy.deepcopy(alt)
        altered['segment']['segment'] = first['segment']['segment']
        altered['segment']['segment_sha256'] = first['segment']['segment_sha256']
        (root / alt['segment']['revision_metadata']).write_text(json.dumps(altered))
        with patch.object(chain.ProjectAssetStore, 'load', side_effect=AssertionError('delivery read live captions')):
            chapter_manifest = delivery.load(chain, chapter['snapshot_json'])
            assert chapter_manifest['chapter']['editorial_origin_frame'] == 24
            chapter_cues = delivery.subtitle_cues(chapter_manifest, 24, 24)
            assert chapter_cues[0]['start'] == 0 and chapter_cues[0]['end'] == .5
            manifest = chain.MiniMaxH3ChainDeliverySource().load(prepared['snapshot_json'])[0]
            assert manifest['editorial']['replacements'][0]['alternate_revision'] == 'a' * 32
            assert delivery.subtitle_cues(manifest, 48, 0)[0]['text'] == 'Frozen caption'
            result = chain.MiniMaxH3ChainAssemble().assemble(manifest, 'generated', 'frozen_cut', 128, blend_schedule='0')
        path = pathlib.Path(result['result'][0] if isinstance(result, dict) else result[0])
        assert path.is_file(), result
        record = json.loads(path.with_suffix('.delivery.json').read_text())
        assert record['snapshot_json'] == prepared['snapshot_json']
        assert record['video_sha256'] == h.digest(path)
        assert record['settings']['audio_source'] == 'generated'
        streams = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(path)]))['streams']
        assert {s['codec_type'] for s in streams} >= {'audio', 'video'}
        assert int(next(s for s in streams if s['codec_type'] == 'video')['nb_frames']) == 48
        assert 'Frozen caption' in path.with_suffix('.srt').read_text()
        # Check that the accepted ALT supplies red pixels, not the now-active blue original.
        pixel = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-frames:v', '1', '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
        assert pixel[0] > pixel[2] + 100, tuple(pixel)
        (run / 'branches' / branch['id'] / 'branch.json').unlink()
        reject(lambda: delivery.load(chain, named_snapshot['snapshot_json']), 'branch')
        # An unavailable immutable source must fail rather than fall back to today's cut.
        (root / alt['segment']['segment']).write_bytes(b'damaged')
        reject(lambda: delivery.load(chain, prepared['snapshot_json']), 'SHA-256')
        print('Frozen delivery: read-only preparation, exact lineage/ALT, caption isolation, uint64 text, corruption rejection and real 48-frame MP4 with audio pass')


if __name__ == '__main__':
    main()
