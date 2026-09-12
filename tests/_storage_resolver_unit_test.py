"""Relocation bridge integrity, identity, recovery and Windows path budgets."""
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import storage_resolver as resolver
import storage_rehearsal as rehearsal
from processing_persistence import atomic_json
from storage_layout import OrganizedStorageLayout


class RelocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lab = Path(self.temp.name)
        self.output = self.lab / "copy/output"
        self.root = self.output / "h3_chains/demo"
        self.root.mkdir(parents=True)
        self.old = "segments/clip_0001." + "a" * 32 + ".mp4"
        self.new = "media/generation/" + "b" * 32 + "/video.mp4"
        self.png = "chapters/01/upscaled/hq/frames/DLSS_2"
        self.export = "exports/png/" + "c" * 32
        self.data = {self.old: b"VIDEO", "checkpoints/clip_0001.json": b'{"seed":18446744073709551615}',
                     self.png + "/frame_00000001.png": b"PNG", self.png + "/export.json": b"{}",
                     self.png + "/.png_export.lock": b""}
        for name, data in self.data.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.receipt = self.lab / "receipt.json"
        atomic_json(self.receipt, {"copy": str(self.root), "source": str(self.lab / "not-the-copy"),
            "independent_copies": True, "files": [{"path": p, "sha256": rehearsal.sha256(self.root / p)}
                                                   for p in self.data]})
        self.proposal = self.lab / "proposal.json"
        mapping = {self.old: {"target": self.new, "sha256": rehearsal.sha256(self.root / self.old)}}
        mapping.update({p: {"target": self.export + p[len(self.png):],
                            "sha256": rehearsal.sha256(self.root / p), "reason": "PNG:" + self.png}
                        for p in self.data if p.startswith(self.png + "/")})
        atomic_json(self.proposal, {"mapping": mapping})

    def prepare(self):
        return rehearsal.prepare(self.receipt, self.proposal, self.lab / "journal")

    def activate(self):
        journal = self.prepare()
        rehearsal.relocate(journal)
        self.addCleanup(lambda: rehearsal.rollback(journal))
        self.access = resolver.rehearsal_access(self.root)
        self.access.__enter__()
        self.addCleanup(self.access.__exit__, None, None, None)
        return journal

    def address(self, path):
        return "h3_chains/demo/" + path

    def test_resolve_reverse_preserves_saved_identity_bytes_and_lock_inode(self):
        lock = self.root / self.png / ".png_export.lock"
        inode = lock.stat().st_ino
        journal = self.activate()
        physical = resolver.resolve_output(self.output, self.address(self.old))
        self.assertEqual(physical, self.root / self.new)
        self.assertEqual(physical.read_bytes(), b"VIDEO")
        self.assertEqual(resolver.logical_output(self.output, physical), self.address(self.old))
        self.assertEqual(resolver.resolve_output(self.output, self.address(self.png)), self.root / self.export)
        self.assertEqual(resolver.resolve_output(self.output, self.root / self.export / ".png_export.lock"), lock)
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertEqual((self.root / "checkpoints/clip_0001.json").read_bytes(), self.data["checkpoints/clip_0001.json"])
        rehearsal.rollback(journal)
        rehearsal.rollback(journal)  # idempotent completed recovery
        for path, raw in self.data.items():
            self.assertEqual((self.root / path).read_bytes(), raw)

    def test_no_production_activation_even_with_valid_aliases(self):
        journal = self.prepare()
        rehearsal.relocate(journal)
        try:
            with self.assertRaisesRegex(resolver.StorageError, "copy-only"):
                resolver.resolve_output(self.output, self.address(self.old))
        finally:
            rehearsal.rollback(journal)

    def test_interrupted_move_blocks_consumers_and_restarts_then_restores(self):
        journal = self.prepare()
        def fail(index):
            if index == 2:
                raise OSError("simulated disconnect")
        with self.assertRaisesRegex(OSError, "disconnect"):
            rehearsal.relocate(journal, after_move=fail)
        with resolver.rehearsal_access(self.root), self.assertRaisesRegex(resolver.StorageError, "incomplete"):
            resolver.resolve_output(self.output, self.address(self.old))
        rehearsal.relocate(journal)
        rehearsal.rollback(journal)
        for path, raw in self.data.items():
            self.assertEqual((self.root / path).read_bytes(), raw)

    def test_concurrent_control_change_refuses_publication_without_overwriting_it(self):
        journal = self.prepare()
        control = self.root / 'checkpoints/clip_0001.json'
        def change(index):
            if index == 1:
                control.write_bytes(b'{"seed":999}')
        with self.assertRaisesRegex(ValueError, "state changed"):
            rehearsal.relocate(journal, after_move=change)
        self.assertEqual(json.loads((self.root/'storage.json').read_text())['phase'], 'moving')
        rehearsal.rollback(journal)
        self.assertEqual(control.read_bytes(), b'{"seed":999}')

    def test_foreign_storage_marker_is_preserved_during_recovery(self):
        journal = self.prepare()
        rehearsal.relocate(journal)
        marker = self.root/'storage.json'
        saved = marker.read_bytes()
        marker.write_bytes(b'{"format":"foreign"}')
        with self.assertRaisesRegex(ValueError, "another operation"):
            rehearsal.rollback(journal)
        self.assertEqual(marker.read_bytes(), b'{"format":"foreign"}')
        marker.write_bytes(saved)
        rehearsal.rollback(journal)

    def test_new_file_while_old_controls_are_unchanged_prevents_publication(self):
        journal = self.prepare()
        def create(index):
            if index == 1:
                (self.root/'checkpoints/clip_0002.json').write_bytes(b'{"new":"scene"}')
        with self.assertRaisesRegex(ValueError,'untracked file'):
            rehearsal.relocate(journal,after_move=create)
        self.assertEqual(json.loads((self.root/'storage.json').read_text())['phase'],'moving')
        rehearsal.rollback(journal)
        self.assertEqual((self.root/'checkpoints/clip_0002.json').read_bytes(),b'{"new":"scene"}')

    def test_rollback_restart_after_final_marker_was_archived(self):
        journal = self.prepare()
        rehearsal.relocate(journal)
        original = rehearsal.atomic_json
        def fail_final(path, value):
            if Path(path) == journal and value.get('phase') == 'restored':
                raise OSError('simulated final journal publication failure')
            return original(path, value)
        with patch.object(rehearsal, 'atomic_json', fail_final), self.assertRaisesRegex(OSError, 'publication'):
            rehearsal.rollback(journal)
        self.assertFalse((self.root/'storage.json').exists())
        rehearsal.rollback(journal)
        self.assertEqual(json.loads(journal.read_text())['phase'], 'restored')

    def test_rollback_collision_preserves_both_then_recovers(self):
        journal = self.prepare()
        rehearsal.relocate(journal)
        original = self.root / self.old
        original.write_bytes(b"external write")
        with self.assertRaisesRegex(ValueError, "collision"):
            rehearsal.rollback(journal)
        self.assertEqual(original.read_bytes(), b"external write")
        self.assertEqual((self.root / self.new).read_bytes(), b"VIDEO")
        original.rename(self.lab / "preserved-external-file")
        rehearsal.rollback(journal)
        self.assertEqual(original.read_bytes(), b"VIDEO")

    def test_alias_corruption_never_falls_back_to_old_path(self):
        journal = self.activate()
        marker = json.loads((self.root / "storage.json").read_text())
        alias = self.root / marker["aliases"]
        raw = alias.read_bytes()
        alias.write_bytes(raw + b" ")
        try:
            with self.assertRaisesRegex(resolver.StorageError, "SHA-256"):
                resolver.resolve_output(self.output, self.address(self.old))
        finally:
            alias.write_bytes(raw)

    def test_unknown_version_blocks_graph_and_branch_readers(self):
        from checkpoint_manager import CheckpointGraphManager
        from working_branches import WorkingBranches
        from project_ownership import ownership_status
        atomic_json(self.root / "storage.json", {"format": "future", "version": 999})
        for call in (lambda: CheckpointGraphManager(self.output).graph("demo", adopt_legacy=False),
                     lambda: WorkingBranches(self.output, "demo").load(),
                     lambda: ownership_status(self.output, "demo")):
            with self.assertRaisesRegex(resolver.StorageError, "Unsupported"):
                call()

    def test_invalid_alias_maps(self):
        base = {"format": resolver.ALIASES, "files": {}, "directories": {}}
        maps = [
            {"../outside": self.new}, {self.old: "../outside"},
            {r"segments\old.mp4": self.new},
            {self.old: self.new, "other": self.new.upper()},
            {self.old: self.new, "other": self.new + "/child"},
            {self.old: "media/a", "media/a": self.old},
        ]
        for files in maps:
            with self.subTest(files=files), self.assertRaises(ValueError):
                resolver.validate_aliases({**base, "files": files})
        with self.assertRaises(ValueError):
            resolver.validate_aliases({**base, "files": {"frames/x/a.png": "exports/png/a.png"},
                                      "directories": {"frames/x": "exports/png/x"}})

    def test_target_symlink_and_missing_authority_rejected(self):
        self.activate()
        target = self.root / self.new
        target.rename(self.lab / "held-video")
        target.symlink_to(self.lab / "held-video")
        try:
            with self.assertRaisesRegex(resolver.StorageError, "symlinks"):
                resolver.resolve_output(self.output, self.address(self.old))
        finally:
            target.unlink()
            (self.lab / "held-video").rename(target)
        marker = json.loads((self.root / "storage.json").read_text())
        alias = self.root / marker["aliases"]
        alias.rename(self.lab / "held-alias")
        try:
            with self.assertRaisesRegex(resolver.StorageError, "missing"):
                resolver.resolve_output(self.output, self.address(self.old))
        finally:
            (self.lab / "held-alias").rename(alias)

    def test_untracked_directory_child_blocks_prepare(self):
        (self.root / self.png / "user-edit.png").write_bytes(b"keep")
        with self.assertRaisesRegex(ValueError, "Untracked"):
            self.prepare()
        self.assertFalse((self.root / "storage.json").exists())

    def test_long_windows_atomic_json_temp_budget(self):
        layout = OrganizedStorageLayout(r"G:\Stability\Data\Packages\ComfyUI\output\h3_chains\silver_estate_final_semantic")
        address = "exports/png/" + "a" * 32 + "/.png_variants/" + "b" * 64 + ".json"
        with self.assertRaisesRegex(ValueError, "246"):
            layout.check_budget(address, staging_suffix="." + "f" * 32 + ".tmp")
        self.assertLessEqual(layout.check_atomic_json_budget(address), 240)
        paths = []
        real_replace = os.replace
        def capture(source, destination):
            paths.append(Path(source).name)
            return real_replace(source, destination)
        with patch("processing_persistence.os.replace", capture):
            atomic_json(self.lab / ("b" * 64 + ".json"), {"seed": 18446744073709551615})
        self.assertTrue(all(re.fullmatch(r"\.tmp-[0-9a-f]{32}", p) for p in paths))

    def test_generation_graph_ownership_uses_logical_revision_paths(self):
        from storage_fixture import composite_project, RUN
        from checkpoint_manager import CheckpointGraphManager
        root = composite_project(self.output)
        manager = CheckpointGraphManager(self.output)
        before = manager.graph(RUN, adopt_legacy=False)
        mapping = {}
        for path in (root/'segments').glob('*.mp4'):
            target = 'media/generation/' + path.stem.split('.')[-1] + '/video.mp4'
            mapping[path.relative_to(root).as_posix()] = target
            destination = root/target
            destination.parent.mkdir(parents=True, exist_ok=True)
            path.rename(destination)
        name = 'project/aliases/'+'f'*32+'.json'
        aliases = {'format':resolver.ALIASES,'files':mapping,'directories':{}}
        atomic_json(root/name, aliases)
        atomic_json(root/'storage.json', {'format':resolver.FORMAT,'version':1,'phase':'ready',
            'mode':'rehearsal','aliases':name,'aliases_sha256':rehearsal.sha256(root/name)})
        with resolver.rehearsal_access(root):
            after = manager.graph(RUN, adopt_legacy=False)
        self.assertEqual(before['summary'], after['summary'])
        self.assertEqual([r['revision'] for r in before['revisions']], [r['revision'] for r in after['revisions']])


if __name__ == "__main__":
    unittest.main()
