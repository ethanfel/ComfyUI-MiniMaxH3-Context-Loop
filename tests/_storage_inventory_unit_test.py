"""Storage Inspector regression and legacy consumer baselines, CPU/disk only."""

import ast
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import storage_inventory as inventory
from storage_fixture import (composite_project, write_json, snapshot, RUN, BASE, ALT,
                             BRANCH, EMPTY_BRANCH, PASS, SEED)


class StorageInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.root = composite_project(self.output)

    def inspect(self, **kwargs):
        return inventory.inspect_storage(self.output, RUN, **kwargs)

    def test_composite_inventory_preserves_every_byte_and_does_not_open_media(self):
        before = snapshot(self.output)
        opened = []
        original = os.open

        def readonly(path, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            self.assertEqual(Path(path).suffix, ".json", "Inspector must never open media/tensors")
            self.assertNotIn(Path(path).name, {"plan.json", "workflow.json", "api_prompt.json", "branch.json"})
            opened.append(str(path))
            return original(path, flags, *args, **kwargs)

        with patch.object(inventory.os, "open", readonly):
            report = self.inspect()
        self.assertTrue(opened)
        self.assertEqual(snapshot(self.output), before)
        self.assertTrue(report["scan_complete"])
        self.assertFalse(report["migration"]["enabled"])
        self.assertNotIn("PRIVATE", json.dumps(report))
        files = {item["path"]: item for item in report["files"]}
        checkpoint = files["checkpoints/clip_0001." + BASE + ".safetensors"]
        self.assertEqual(checkpoint["referencing_branches"], [BRANCH, "main"])
        self.assertIn("shared_reference", checkpoint["flags"])
        self.assertEqual(report["totals"]["logical_bytes"], sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file()))
        self.assertEqual(report["totals"]["files"], len(files))
        self.assertIn("unclassified", {row["category"] for row in files.values()})
        self.assertEqual(report["issue_counts"], {"external_reference": 1, "size_mismatch": 1})
        self.assertTrue(any("retired" in row["flags"] for row in files.values()))
        self.assertEqual(files["frames/export_2/frame_00000001.png"]["flags"], ["possible_edit", "referenced", "unverified"])
        self.assertTrue(any(row["source_revision"] == ALT and row["full_latent"] == "declared_omitted"
                            for row in report["records"] if row["revision"] == PASS))
        self.assertFalse(any(row["branch_id"] == EMPTY_BRANCH for row in report["records"]))
        self.assertEqual(report["inventory_stat_id"], self.inspect()["inventory_stat_id"])

    def test_existing_graph_branch_and_processing_consumers_are_unchanged(self):
        from checkpoint_manager import CheckpointGraphManager
        from checkpoint_variants import saved_checkpoint_variants
        from working_branches import WorkingBranches
        graph = CheckpointGraphManager(self.output)
        branches = WorkingBranches(self.output, RUN)

        def consumer_view():
            original = graph.graph(RUN, adopt_legacy=False)
            return {"graph": original, "branches": branches.listing(),
                    "processing": saved_checkpoint_variants(self.output, RUN, original["revisions"])}

        before = consumer_view()
        self.assertGreaterEqual(len(before["graph"]["revisions"]), 3)
        self.assertEqual(len(before["processing"]["variants"]), 1)
        disk = snapshot(self.output)
        self.inspect()
        self.assertEqual(consumer_view(), before)
        self.assertEqual(snapshot(self.output), disk)
        self.assertEqual(json.loads((self.root / "plan.json").read_text())["seed"], SEED)

    def test_missing_invalid_foreign_and_external_paths_never_read_other_projects(self):
        write_json(self.root / "manifest.json", {"paths": [
            {"checkpoint": "h3_chains/" + RUN + "/checkpoints/absent.safetensors"},
            {"checkpoint": "h3_chains/foreign/checkpoints/secret.json"},
            {"checkpoint": "../SECRET"}, {"checkpoint": r"C:\SECRET"}]})
        write_json(self.root / "checkpoints/foreign.json", {"run_name": "foreign", "path": "SECRET"})
        report = self.inspect()
        self.assertEqual(report["issue_counts"]["missing_reference"], 1)
        self.assertEqual(report["issue_counts"]["invalid_reference"], 1)
        self.assertEqual(report["issue_counts"]["external_reference"], 3)
        self.assertEqual(report["issue_counts"]["foreign_metadata"], 1)
        self.assertNotIn("SECRET", json.dumps(report))

    def test_no_follow_directory_file_symlinks_or_project_links(self):
        outside = self.output / "outside"
        outside.mkdir()
        (outside / "secret.json").write_text("PRIVATE")
        (self.root / "linked").symlink_to(outside, target_is_directory=True)
        (self.root / "secret.json").symlink_to(outside / "secret.json")
        report = self.inspect()
        self.assertFalse(report["scan_complete"])
        self.assertEqual(report["issue_counts"]["link_skipped"], 2)
        self.assertNotIn("PRIVATE", json.dumps(report))
        (self.output / "h3_chains/link").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            inventory.inspect_storage(self.output, "link")

    def test_simulated_windows_junction_is_not_followed(self):
        original = inventory.is_link_or_junction
        with patch.object(inventory, "is_link_or_junction", lambda path:
                          path.name == "project_assets" or original(path)):
            report = self.inspect()
        self.assertEqual(report["issue_counts"]["link_skipped"], 1)
        self.assertNotIn("project_assets/face.png", [row["path"] for row in report["files"]])

    def test_pending_transactions_malformed_metadata_and_limits_never_recover(self):
        pending = self.root / "frames/export/.png_pending.json"
        write_json(pending, {"format": "pending"})
        (self.root / "checkpoints/broken.json").write_text("{")
        before = snapshot(self.output)
        report = self.inspect()
        self.assertEqual(report["issue_counts"]["pending_transaction"], 1)
        self.assertEqual(report["issue_counts"]["unreadable_metadata"], 1)
        self.assertFalse(report["scan_complete"])
        self.assertFalse(self.inspect(max_files=1)["scan_complete"])
        with patch.object(inventory, "MAX_JSON_BYTES", 1):
            self.assertIn("metadata_limit", self.inspect()["issue_counts"])
        with patch.object(inventory, "MAX_REFERENCES", 1):
            self.assertIn("reference_limit", self.inspect()["issue_counts"])
        self.assertEqual(snapshot(self.output), before)

    def test_changed_during_scan_is_not_presented_as_complete(self):
        original = inventory._Inventory.inspect_document
        changed = False

        def change(scanner, document, value):
            nonlocal changed
            original(scanner, document, value)
            if not changed:
                (self.root / "late.txt").write_bytes(b"new writer")
                changed = True

        with patch.object(inventory._Inventory, "inspect_document", change):
            report = self.inspect()
        self.assertFalse(report["scan_complete"])
        self.assertIn("changed_during_scan", report["issue_counts"])

    def test_hardlinks_report_per_path_allocation_without_claiming_dedup(self):
        target = self.root / "project_assets/face.png"
        os.link(target, self.root / "project_assets/shared.png")
        report = self.inspect()
        rows = [row for row in report["files"] if "hardlinked_in_project" in row["flags"]]
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row["logical_bytes"] for row in rows), target.stat().st_size * 2)

    def test_empty_and_missing_project_and_portable_run_validation(self):
        (self.output / "h3_chains/empty").mkdir()
        report = inventory.inspect_storage(self.output, "empty")
        self.assertTrue(report["scan_complete"])
        self.assertEqual(report["totals"]["files"], 0)
        with self.assertRaises(FileNotFoundError):
            inventory.inspect_storage(self.output, "missing")
        for run in ("../outside", "", "a/b", r"a\b", "C:secret", None):
            with self.subTest(run=run), self.assertRaises(ValueError):
                inventory.inspect_storage(self.output, run)

    def test_http_route_is_read_only_unscoped_and_maps_errors(self):
        # Execute the exact production handler without importing ComfyUI/models.
        tree = ast.parse((ROOT / "chain_nodes.py").read_text())
        handler = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                       and node.name == "_inspect_project_storage")
        package = types.ModuleType("storage_api_fixture")
        package.__path__ = [str(ROOT)]
        async def to_thread(function, *args):
            # Handler contract test; disk implementation is tested separately.
            # No dependency on sandbox thread-pool wakeup sockets.
            return function(*args)

        scope = {"__package__": package.__name__, "asyncio": types.SimpleNamespace(to_thread=to_thread),
                 "_output_root": lambda: self.output, "_strict_run_name": lambda value: value,
                 "web": types.SimpleNamespace(json_response=lambda data, status=200, **kwargs: (status, data, kwargs))}
        with patch.dict(sys.modules, {package.__name__: package,
                                    package.__name__ + ".storage_inventory": inventory}):
            exec(compile(ast.Module(body=[handler], type_ignores=[]), "chain_nodes.py", "exec"), scope)
            for run, expected in ((RUN, 200), ("missing", 404), ("../other", 400)):
                result = asyncio.run(scope[handler.name](types.SimpleNamespace(query={"run_name": run})))
                self.assertEqual(result[0], expected)
                if expected == 200:
                    self.assertEqual(result[2]["headers"]["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
