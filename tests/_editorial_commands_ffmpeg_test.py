"""Saved-cut review and real native assembly agree on frames, ALT, gap and SRT."""
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('editorial_media_helpers', ROOT / 'tests/_checkpoint_revision_unit_test.py')
h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
chain = h.chain
commands = importlib.import_module(chain.__package__ + '.editorial_commands')
import torch
from safetensors.torch import save_file

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp); h.folder_paths.output_directory = tmp
    run = root / 'h3_chains/cut_media'
    compatibility = {'width': 64, 'height': 48, 'fps': 24, 'audio_mode': 'generated_audio',
                     'context_length': 0, 'audio_context_length': 0, 'video_blend_frames': 0}
    def media(scene, revision, color, parent=None, alternate=False):
        record, _ = h.write_revision(run, scene, revision, 18446744073709551615, active=not alternate,
            predecessor=parent, run_name=run.name, compatibility=compatibility,
            context_length=0, audio_context_length=0)
        segment = record['segment']; segment.update(raw_frames=124, delivered_frames=108, sample_rate=8000)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
            f'color=c={color}:s=64x48:r=24:d=4.5', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
            str(root / segment['segment'])], check=True)
        frequency = 660 if alternate else scene * 220
        wave = torch.sin(torch.arange(36000) * (2 * torch.pi * frequency / 8000)).reshape(1, 1, -1).repeat(1, 2, 1) * .1
        save_file({'delivered_audio': wave}, str(root / segment['checkpoint']))
        segment['segment_sha256'] = h.digest(root / segment['segment'])
        segment['checkpoint_sha256'] = h.digest(root / segment['checkpoint'])
        if alternate:
            segment.update(take_kind='editorial_alternate', alternate_of_revision='1' * 32)
        (root / segment['revision_metadata']).write_text(json.dumps(record))
        if not alternate: (root / segment['metadata']).write_text(json.dumps(record))
        return record
    first = media(1, '1' * 32, 'blue')
    media(2, '2' * 32, 'green', first)
    media(1, 'a' * 32, 'red', alternate=True)
    (run / 'plan.json').write_text(json.dumps({'run_name': run.name, 'shots': [{'id': 'scene_1'}, {'id': 'scene_2'}]}))
    (run / 'editorial.json').write_text(json.dumps({'revision': 'e' * 32,
        'scene_order': [{'scene': 1, 'scene_id': 'scene_1'}, {'scene': 2, 'scene_id': 'scene_2'}],
        'chapters': [{'id': 'one', 'start_scene_id': 'scene_1'}, {'id': 'two', 'start_scene_id': 'scene_2'}],
        'replacements': [{'scene': 1, 'scene_id': 'scene_1', 'base_revision': '1' * 32,
                          'alternate_revision': 'a' * 32, 'media_mode': 'picture_only'}],
        'subtitles': {'mode': 'preview_srt', 'asset_id': 'lyrics', 'offset_seconds': .5}}))
    catalog = root / 'h3_projects' / run.name / 'catalog.json'; catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps({'format': 'h3_project_assets_v1', 'version': 1, 'project': run.name,
        'assets': [{'id': 'lyrics', 'kind': 'audio', 'tag': 'lyrics',
                    'lyrics': '1\n00:00:01,000 --> 00:00:03,000\nAligned words'}]}))
    owner = chain.claim_project_ownership(tmp, run.name, 'media-test-owner')
    proof = {'owner_id': 'media-test-owner', 'epoch': owner['epoch']}
    base = {'run_name': run.name, 'branch_id': 'main'}
    def edit(patch):
        view = commands.command(chain, {**base, 'action': 'inspect'})
        request = {**base, 'stamp': view['stamp'], 'patch': {'scene': patch}}
        preview = commands.command(chain, {**request, 'action': 'preview'})
        return commands.command(chain, {**request, 'action': 'apply', 'preview_token': preview['preview_token']}, proof)
    edit({'scene': 1, 'scene_id': 'scene_1', 'revision': '1' * 32, 'out_frame': 48})
    final = edit({'scene': 2, 'scene_id': 'scene_2', 'revision': '2' * 32, 'start_frame': 60})
    assert not final['timeline']['stale_scenes']
    assert [(r['kind'], r['start_frame'], r['frame_count']) for r in final['timeline']['records']] == [('scene', 0, 48), ('gap', 48, 12), ('scene', 60, 108)]
    assert final['timeline']['frames'] == 168
    manifest = chain._checkpoint_selection_manifest({'run_name': run.name, 'output_mode': 'workflow_local',
        'final_cut_branch_id': 'main', 'lineage': [{'scene': 1, 'revision': '1' * 32}, {'scene': 2, 'revision': '2' * 32}]})
    result = chain.MiniMaxH3ChainAssemble().assemble(manifest, 'generated', 'edited_cut', 128, blend_schedule='0')
    video = Path(result['result'][0] if isinstance(result, dict) else result[0])
    streams = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(video)]))['streams']
    assert {s['codec_type'] for s in streams} >= {'video', 'audio'}
    assert int(next(s for s in streams if s['codec_type'] == 'video')['nb_frames']) == 168
    pixel = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(video), '-frames:v', '1', '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    assert pixel[0] > pixel[2] + 100, tuple(pixel)
    gap = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', '2.1', '-i', str(video), '-frames:v', '1', '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    assert max(gap) < 8, tuple(gap)
    def samples(start, duration):
        raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(start), '-i', str(video),
            '-t', str(duration), '-vn', '-ac', '1', '-ar', '8000', '-f', 'f32le', '-'])
        return torch.frombuffer(bytearray(raw), dtype=torch.float32)
    for start, expected in ((.2, 220), (2.7, 440)):
        wave = samples(start, 1)
        peak = int(torch.fft.rfft(wave).abs().argmax()) * 8000 / len(wave)
        assert abs(peak - expected) < 4, (start, peak)
    assert float(samples(2.05, .3).square().mean().sqrt()) < .005
    assert '00:00:01,500 --> 00:00:03,500' in video.with_suffix('.srt').read_text()
    assert 'Aligned words' in video.with_suffix('.srt').read_text()
print('Native editorial media: review and 168-frame FFmpeg assembly agree on trimmed ALT, black gap, generated audio and shifted SRT')
