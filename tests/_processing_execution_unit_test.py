"""CPU-only execution provenance checks; no ComfyUI or model imports."""

import hashlib
import importlib.util
import json
import logging
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "processing_execution", ROOT / "processing_execution.py")
execution = importlib.util.module_from_spec(spec)
spec.loader.exec_module(execution)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.documents = {}
        self.chain = SimpleNamespace(
            _json_document=lambda value: json.loads(json.dumps(value)),
            _fingerprint=fingerprint,
            _absolute_output_path=lambda value: value,
            _read_json=Mock(side_effect=lambda path: self.documents[path]),
            _LOG=logging.getLogger("processing-execution-test"),
        )

    def scene(self, index, settings):
        payload = {"workflow": {"nodes": [settings]},
                   "api_prompt": {"5": {"class_type": "DLSS5", "inputs": settings}}}
        segment = {"index": index, "revision": str(index) * 32,
                   "revision_metadata": "scene%d.json" % index,
                   "execution_hash": fingerprint(payload)}
        self.documents[segment["revision_metadata"]] = {
            "segment": segment, "execution": payload}
        return segment, payload

    def test_capture_clones_both_formats_and_ignores_unrelated_extra_data(self):
        prompt = {"5": {"class_type": "DLSS5", "inputs": {"seed": 2**63 + 7}}}
        workflow = {"nodes": [{"id": 5, "widgets_values": ["quality", 2]}]}
        saved = execution.capture(self.chain, prompt, {
            "workflow": workflow, "unrelated_private_data": "not archived"})
        self.assertEqual(saved, {"api_prompt": prompt, "workflow": workflow})
        prompt["5"]["inputs"]["seed"] = 0
        workflow["nodes"].clear()
        tags = execution.media_tags(saved)
        self.assertEqual(json.loads(tags["prompt"])["5"]["inputs"]["seed"], 2**63 + 7)
        self.assertTrue(json.loads(tags["workflow"])["nodes"])
        self.assertNotIn("unrelated_private_data", saved)

    def test_api_only_does_not_invent_ui_workflow(self):
        saved = execution.capture(self.chain, {"1": {"inputs": {}}})
        self.assertEqual(set(execution.media_tags(saved)), {"prompt"})
        self.assertEqual(execution.capture(self.chain), {})
        self.assertEqual(execution.capture(self.chain, {}), {})

    def test_invalid_document_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "JSON object"):
            execution.capture(self.chain, [])

    def test_nonfinite_ui_values_are_opaque_but_roundtrip_to_media_tags(self):
        first, payload = self.scene(1, {'widget':[float('inf'),float('nan'),2**64-1]})
        fields = execution.metadata_fields(payload)
        self.assertEqual(set(fields),{'execution_json'})
        self.documents[first['revision_metadata']] = dict(segment=first, **fields)
        raw = json.dumps(self.documents[first['revision_metadata']], allow_nan=False)
        self.assertTrue(raw)
        tags = execution.assembly_tags(self.chain,[first])
        restored = json.loads(tags['workflow'])['nodes'][0]['widget']
        self.assertTrue(math.isinf(restored[0]) and math.isnan(restored[1]))
        self.assertEqual(restored[2],2**64-1)
        self.assertEqual(execution.metadata_fields({'workflow':{'nodes':[]}}),
                         {'execution':{'workflow':{'nodes':[]}}})

    def test_ambiguous_or_malformed_opaque_execution_is_rejected(self):
        for document in ({'execution':{},'execution_json':{}},
                {'execution_json':{'unexpected':'{}'}}, {'execution_json':{'workflow':{}}},
                {'execution_json':{'workflow':'[]'}}, {'execution_json':{'workflow':'broken'}}):
            with self.subTest(document=document), self.assertRaises(ValueError):
                execution.from_metadata(document)

    def test_resumed_scenes_keep_distinct_settings(self):
        first, old = self.scene(8, {"scale": 2, "preset": "old"})
        second, new = self.scene(9, {"scale": 3, "preset": "new"})
        tags = execution.assembly_tags(self.chain, [first, second])
        self.assertEqual(json.loads(tags["workflow"]), new["workflow"])
        table = json.loads(tags["h3_processing_workflows"])
        self.assertEqual(table["standard_tags_scene"], 9)
        self.assertEqual(table["executions"][first["execution_hash"]], old)
        self.assertEqual(table["executions"][second["execution_hash"]], new)
        self.assertEqual([s["scene"] for s in table["scenes"]], [8, 9])

    def test_identical_snapshots_are_embedded_once(self):
        first, _ = self.scene(1, {"scale": 2})
        second, _ = self.scene(2, {"scale": 2})
        tags = execution.assembly_tags(self.chain, [first, second])
        table = json.loads(tags["h3_processing_workflows"])
        self.assertEqual(len(table["executions"]), 1)
        self.assertEqual(len(table["scenes"]), 2)

    def test_legacy_scene_does_not_borrow_neighbour_workflow(self):
        first, _ = self.scene(1, {"scale": 2})
        legacy = {"index": 2, "revision": "b" * 32}
        tags = execution.assembly_tags(self.chain, [first, legacy])
        self.assertNotIn("workflow", tags)
        self.assertNotIn("prompt", tags)
        table = json.loads(tags["h3_processing_workflows"])
        self.assertIsNone(table["standard_tags_scene"])
        self.assertIsNone(table["scenes"][1]["execution_hash"])
        self.assertEqual(len(table["executions"]), 1)

    def test_corrupt_snapshot_is_not_embedded(self):
        first, payload = self.scene(1, {"scale": 2})
        payload["workflow"]["wrong"] = True
        with self.assertLogs(self.chain._LOG, level="WARNING"):
            tags = execution.assembly_tags(self.chain, [first])
        self.assertNotIn("workflow", tags)
        self.assertEqual(json.loads(tags["h3_processing_workflows"])["executions"], {})

    def test_wrong_revision_is_not_embedded(self):
        first, _ = self.scene(1, {"scale": 2})
        with self.assertLogs(self.chain._LOG, level="WARNING"):
            tags = execution.assembly_tags(self.chain, [{**first, "revision": "b" * 32}])
        self.assertNotIn("workflow", tags)

    def test_missing_checkpoint_does_not_prevent_video_assembly(self):
        first, _ = self.scene(1, {"scale": 2})
        self.chain._read_json.side_effect = FileNotFoundError("removed metadata")
        with self.assertLogs(self.chain._LOG, level="WARNING"):
            tags = execution.assembly_tags(self.chain, [first])
        self.assertNotIn("workflow", tags)

    def test_malformed_checkpoint_is_not_embedded(self):
        first, _ = self.scene(1, {"scale": 2})
        for value in ([], {"segment": "invalid", "execution": {}}):
            self.documents[first["revision_metadata"]] = value
            with self.assertLogs(self.chain._LOG, level="WARNING"):
                tags = execution.assembly_tags(self.chain, [first])
            self.assertNotIn("workflow", tags)


if __name__ == "__main__":
    unittest.main()
