"""Preview reads may use catalog mirrors without repairing project files."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_assets import ProjectAssetStore, PROJECT_ASSET_FORMAT


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else None
            for p in root.rglob('*')}


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    store = ProjectAssetStore(str(root / 'input'), str(root / 'output'))
    primary = root / 'input/h3_projects/film/catalog.json'
    backup = root / 'output/h3_chains/film/project_assets/catalog.json'
    backup.parent.mkdir(parents=True)
    catalog = {'format': PROJECT_ASSET_FORMAT, 'version': 1, 'project': 'film',
               'revision': 'saved', 'assets': [{'id': 'lyrics', 'kind': 'audio', 'lyrics': 'saved'}]}
    backup.write_text(json.dumps(catalog))
    for damaged_primary in (False, True):
        if damaged_primary:
            primary.parent.mkdir(parents=True)
            primary.write_text('broken catalog')
        before = tree(root)
        with patch('project_assets._atomic_json', side_effect=AssertionError('preview wrote catalog')):
            assert store.load('film', repair=False)['assets'] == catalog['assets']
        assert tree(root) == before
    # Ordinary reads retain existing recovery behavior.
    assert store.load('film')['revision'] == 'saved'
    assert json.loads(primary.read_text())['revision'] == 'saved'
    primary.write_text(json.dumps({**catalog, 'revision': 'newer'}))
    assert store.load('film', repair=False)['revision'] == 'newer'
    before = tree(root)
    try:
        store.load('empty', create=True, repair=False)
    except ValueError:
        pass
    else:
        raise AssertionError('Read-only load allowed project creation')
    assert tree(root) == before
print('Read-only catalog: missing/corrupt primary, backup isolation, newer primary and ordinary recovery pass')
