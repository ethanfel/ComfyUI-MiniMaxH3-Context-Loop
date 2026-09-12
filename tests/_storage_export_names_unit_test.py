"""Export labels stay readable without changing saved identity or file bytes."""
import concurrent.futures
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import storage_export_names as names
import storage_migrate as migrate


class NameTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='h3-names-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'output/h3_chains/demo'
        self.root.mkdir(parents=True)

    def reserve(self, label='My_export', identity=None, kind='png', **kwargs):
        return names.reserve_base(self.root, kind, identity or uuid.uuid4().hex, label, **kwargs)

    def test_readable_numbering_and_exact_retry(self):
        identity = uuid.uuid4().hex
        first = self.reserve(identity=identity)
        self.assertEqual(first, 'exports/png/My_export')
        self.assertEqual(self.reserve(), 'exports/png/My_export_2')
        self.assertEqual(self.reserve(), 'exports/png/My_export_3')
        self.assertEqual(self.reserve(identity=identity), first)
        with self.assertRaises(names.state.StateConflict):
            self.reserve('different', identity=identity)

    def test_case_and_whole_sidecar_family_collision(self):
        directory = self.root/'exports/video'
        directory.mkdir(parents=True)
        original = directory/'MY_EXPORT.srt'
        original.write_bytes(b'preserve')
        result = self.reserve(kind='video', suffixes=('.mp4','.generated.wav','.srt'))
        self.assertEqual(result, 'exports/video/My_export_2')
        self.assertEqual(original.read_bytes(), b'preserve')

    def test_crash_between_claim_and_receipt_keeps_claimed_name(self):
        original = names.state._immutable
        identity = uuid.uuid4().hex
        def stop(project, path, *args):
            if '/export-name-' in path:
                raise OSError('receipt interrupted')
            return original(project, path, *args)
        with patch.object(names.state, '_immutable', side_effect=stop):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                self.reserve(identity=identity)
        self.assertEqual(self.reserve(), 'exports/png/My_export_2')
        self.assertEqual(self.reserve(identity=identity), 'exports/png/My_export')

    def test_concurrent_exporters_get_distinct_names(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _:self.reserve(), range(4)))
        self.assertEqual(set(results), {'exports/png/My_export'+suffix for suffix in ('','_2','_3','_4')})

    def test_windows_safe_labels_unicode_and_budget(self):
        self.assertEqual(names.readable_name('CON'), '_CON')
        self.assertEqual(names.readable_name('chapter:1/a\\b '), 'chapter_1_a_b')
        self.assertEqual(self.reserve('Épisode 1'), 'exports/png/Épisode 1')
        with self.assertRaises(ValueError):
            self.reserve('x'*240)
        with self.assertRaises(ValueError):
            self.reserve('..')

    def test_symbolic_link_export_parent_is_rejected(self):
        (self.root/'exports').symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.reserve()

    def test_migration_keeps_video_sidecars_grouped_and_png_label(self):
        prefix = 'h3_chains/demo/'
        branch = 'branches/'+'a'*32+'/'
        rows = []
        for root in ('', branch):
            index = self.root/(root+'frames/DLSS/export.json')
            index.parent.mkdir(parents=True)
            index.write_bytes(names.state._encode(dict(format='h3_video_png_sequence_v1')))
            rows.append(dict(source=index.relative_to(self.root).as_posix(), target=prefix+root+'frames/DLSS/export.json',
                             role='control', size=index.stat().st_size, sha256=names.state._hash(index.read_bytes())))
            for address in ('frames/DLSS/frame_00000001.png', 'final/Final.mp4', 'final/Final.generated.wav', 'final/Final.srt'):
                rows.append(dict(source=root+address, target=prefix+root+address, role='payload', size=1, sha256='a'*64))
        _, targets = migrate._inventory(self.root, rows, self.root.parent/'copy', 240)
        for root, suffix in (('', ''), (branch, '_2')):
            self.assertEqual(targets[root+'frames/DLSS/frame_00000001.png']['target'], 'exports/png/DLSS'+suffix+'/frame_00000001.png')
            for extension in ('.mp4','.generated.wav','.srt'):
                self.assertEqual(targets[root+'final/Final'+extension]['target'], 'exports/video/Final'+suffix+extension)
        self.assertEqual(migrate._inventory(self.root, list(reversed(rows)), self.root.parent/'copy', 240)[1], targets)


if __name__ == '__main__':
    unittest.main()
