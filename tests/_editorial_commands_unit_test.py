"""Native saved-cut previews, exact continuation impact and conditional writes."""
import asyncio
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('editorial_test_helpers', ROOT / 'tests/_checkpoint_revision_unit_test.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
chain = h.chain
commands = importlib.import_module(chain.__package__ + '.editorial_commands')


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else None for p in root.rglob('*')}


def reject(call, phrase):
    try:
        call()
    except (ValueError, OSError) as exc:
        assert phrase in str(exc), str(exc)
    else:
        raise AssertionError('Expected rejection: ' + phrase)


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    h.folder_paths.output_directory = tmp
    run = root / 'h3_chains/cut_test'
    previous, pointer_bytes = None, {}
    for index in range(1, 4):
        record, _ = h.write_revision(run, index, str(index) * 32, 18446744073709551615,
                                     active=True, predecessor=previous, run_name=run.name)
        record['segment'].update(raw_frames=124, delivered_frames=108,
            resolved_context_length=39 if index > 1 else 0, resolved_audio_context_length=0,
            generated_continuity='off', predecessor_editorial_out_frames=108)
        for key in ('metadata', 'revision_metadata'):
            path = root / record['segment'][key]
            path.write_text(json.dumps(record))
            pointer_bytes[str(path)] = path.read_bytes()
        previous = record
    path = run / 'editorial.json'
    original = {'run_name': run.name, 'revision': 'e' * 32,
        'scene_order': [{'scene': n, 'scene_id': f'scene_{n}'} for n in range(1, 4)],
        'chapters': [{'id': 'opening', 'title': 'Opening', 'start_scene_id': 'scene_1', 'text': 'Keep notes'}],
        'subtitles': {'mode': 'preview_srt', 'asset_id': 'lyrics', 'offset_seconds': 0}}
    path.write_text(json.dumps(original))
    backup = run / 'project_assets/catalog.json'
    backup.parent.mkdir()
    catalog = {'format': 'h3_project_assets_v1', 'version': 1, 'project': run.name, 'revision': 'catalog-1',
        'assets': [{'id': 'lyrics', 'tag': 'song', 'kind': 'audio',
                    'lyrics': '1\n00:00:01,000 --> 00:00:02,000\nSaved words'}]}
    backup.write_text(json.dumps(catalog))
    owner = 'editorial-test-workflow'
    ownership = chain.claim_project_ownership(tmp, run.name, owner)
    proof = {'owner_id': owner, 'epoch': ownership['epoch']}
    base = {'run_name': run.name, 'branch_id': 'main'}
    before = tree(root)
    inspected = commands.command(chain, {**base, 'action': 'inspect'})
    assert inspected['scenes'][0]['safe_out_frames'] and 48 in inspected['scenes'][0]['safe_out_frames']
    assert 47 not in inspected['scenes'][0]['safe_out_frames']
    patch = {'scene': {'scene': 1, 'scene_id': 'scene_1', 'revision': '1' * 32, 'out_frame': 48}}
    request = {**base, 'stamp': inspected['stamp'], 'patch': patch}
    preview = commands.command(chain, {**request, 'action': 'preview'})
    assert tree(root) == before, 'inspection or preview changed project files'
    assert preview['timeline']['frames'] == 264
    assert [v['scene'] for v in preview['timeline']['stale_scenes']] == [2, 3]
    assert 'depends on stale scene 2' in preview['timeline']['stale_scenes'][1]['reasons']
    assert not (root / 'h3_projects').exists(), 'preview repaired the catalog'
    invalid = {**patch['scene'], 'out_frame': 47}
    reject(lambda: commands.command(chain, {**request, 'action': 'preview', 'patch': {'scene': invalid}}), 'native video/audio boundary')
    apply = {**request, 'action': 'apply', 'preview_token': preview['preview_token']}
    reject(lambda: commands.command(chain, apply), 'read-only')
    saved = commands.command(chain, apply, proof)
    assert saved['editorial']['trims'][0]['out_frame'] == 48
    assert saved['editorial']['chapters'][0]['text'] == 'Keep notes'
    assert all(Path(filename).read_bytes() == content for filename, content in pointer_bytes.items())
    reject(lambda: commands.command(chain, apply, proof), 'changed')
    segments = commands.state(chain, run.name, 'main')['segments']
    reject(lambda: chain._editorial_timeline_records(run.name, segments, saved['editorial']), 'older editorial endpoint')

    def edit(patch):
        current = commands.command(chain, {**base, 'action': 'inspect'})
        request = {**base, 'stamp': current['stamp'], 'patch': patch}
        preview = commands.command(chain, {**request, 'action': 'preview'})
        return commands.command(chain, {**request, 'action': 'apply', 'preview_token': preview['preview_token']}, proof)

    # Reset duration, move the original first picture after the second, and lock it.
    edit({'scene': {**patch['scene'], 'out_frame': None, 'start_frame': 400}})
    locked = edit({'scene': {'scene': 1, 'scene_id': 'scene_1', 'revision': '1' * 32, 'locked': True}})
    assert not locked['timeline']['stale_scenes']
    assert [r['scene'] for r in locked['timeline']['records'] if r['kind'] == 'scene'] == [2, 3, 1]
    current = commands.command(chain, {**base, 'action': 'inspect'})
    reject(lambda: commands.command(chain, {**base, 'action': 'preview', 'stamp': current['stamp'], 'patch': patch}), 'Unlock')
    captions = edit({'subtitles': {'offset_seconds': -0.5}})
    assert captions['timeline']['cues'][0]['start'] == .5
    assert captions['editorial']['locked_scene_ids'] == ['scene_1']
    # Catalog changes invalidate a reviewed patch before it writes.
    current = commands.command(chain, {**base, 'action': 'inspect'})
    request = {**base, 'stamp': current['stamp'], 'patch': {'subtitles': {'mode': 'off'}}}
    preview = commands.command(chain, {**request, 'action': 'preview'})
    catalog['assets'][0]['lyrics'] = 'changed outside the companion'
    backup.write_text(json.dumps(catalog))
    reject(lambda: commands.command(chain, {**request, 'action': 'apply', 'preview_token': preview['preview_token']}, proof), 'assets changed')
    catalog['assets'][0]['lyrics'] = '1\n00:00:01,000 --> 00:00:02,000\nSaved words'
    backup.write_text(json.dumps(catalog))
    current = commands.command(chain, {**base, 'action': 'inspect'})
    request = {**base, 'stamp': current['stamp'], 'patch': {'subtitles': {'mode': 'off'}}}
    preview = commands.command(chain, {**request, 'action': 'preview'})
    pointer = run / 'checkpoints/clip_0003.json'
    old_pointer = pointer.read_bytes()
    metadata = json.loads(old_pointer); metadata['segment']['seed'] = 'another saved input'
    pointer.write_text(json.dumps(metadata))
    reject(lambda: commands.command(chain, {**request, 'action': 'apply', 'preview_token': preview['preview_token']}, proof), 'checkpoints or assets changed')
    pointer.write_bytes(old_pointer)
    # A named branch owns its editorial changes without touching Original.
    authored = {'plan_json': json.dumps({'shots': [{'id': f'scene_{n}'} for n in range(1, 4)]})}
    branch = chain.WorkingBranches(tmp, run.name).create('main', 'Separate cut', authored, 3)
    named = {**base, 'branch_id': branch['id']}
    original_bytes = path.read_bytes()
    current = commands.command(chain, {**named, 'action': 'inspect'})
    named_request = {**named, 'stamp': current['stamp'], 'patch': {'subtitles': {'mode': 'off'}}}
    named_preview = commands.command(chain, {**named_request, 'action': 'preview'})
    commands.command(chain, {**named_request, 'action': 'apply', 'preview_token': named_preview['preview_token']}, proof)
    assert path.read_bytes() == original_bytes
    assert json.loads((run / 'branches' / branch['id'] / 'editorial.json').read_text())['subtitles']['mode'] == 'off'
    assert chain.current_branch(run.name) == 'main'
    # Route serialization and its error statuses use real async worker dispatch.
    response = asyncio.run(chain._editorial_command(h.JsonRequest({**base, 'action': 'inspect'})))
    assert response.status == 200 and json.loads(response.text)['version'] == 1
    response = asyncio.run(chain._editorial_command(h.JsonRequest({**request, 'stamp': 'stale', 'action': 'preview'})))
    assert response.status == 409
print('Saved editorial: read-only previews, native trim grid, transitive continuation impact, locks, captions, ownership and stale-source rejection pass')
