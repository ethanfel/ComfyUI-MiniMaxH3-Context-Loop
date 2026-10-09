#!/usr/bin/env python3
"""Issue #108: authored scene names and canonical context IDs stay equivalent."""
import copy
import json
import unittest

from _checkpoint_revision_unit_test import chain


class SceneSourceNamesTests(unittest.TestCase):
    @staticmethod
    def normalize(document):
        return chain._normalize_plan(
            json.dumps(document), "scene_source_names", 64, 64, 39,
            "video", "head", "disabled", "generated_audio", 39,
            2.0, 8, 7, 18, "test-stack", 0, "guide")

    @staticmethod
    def document(name="Street Ride"):
        return {"shots": [
            {"id": name, "prompt": "First scene.", "length": 90},
            {"id": "Middle", "prompt": "Middle scene.", "length": 90},
            {"id": "End", "prompt": "Final scene.", "length": 90},
        ]}

    def test_existing_context_take_names_compile_and_round_trip(self):
        for name in ("Street Ride", "  Street / Ride!  ", "Long Scene " * 12):
            with self.subTest(name=name):
                document = self.document(name)
                pin = {"source": name, "revision": "a" * 32}
                document["shots"][2]["context_take"] = pin
                before = copy.deepcopy(document)
                actual = self.normalize(document)
                canonical = chain._safe_name(name)
                self.assertEqual(actual["shots"][2]["context_take"],
                                 {"source": canonical, "revision": "a" * 32})
                self.assertEqual(document, before, "Never mutate authored input")
                expected = copy.deepcopy(document)
                expected["shots"][0]["id"] = canonical
                expected["shots"][2]["context_take"]["source"] = canonical
                self.assertEqual(actual, self.normalize(expected))
                restored = self.normalize(chain._effective_editor_plan(actual))
                self.assertEqual(restored["shots"][2]["context_take"],
                                 actual["shots"][2]["context_take"])
                self.assertEqual(chain._history_hash(actual, 3),
                                 chain._history_hash(restored, 3))

    def test_visual_audio_and_composed_links_share_normalization(self):
        links = [
            {"visual_context_source": "Street Ride"},
            {"visual_context_lead_source": "Street Ride", "visual_context_lead_frames": 5},
            {"visual_context_blocks": [{"source": "Street Ride", "frames": 17},
                                       {"source": "Middle", "frames": 22}]},
            {"audio_context_unlocked": True, "audio_context_source": "Street Ride"},
            {"audio_context_unlocked": True, "audio_context_lead_source": "Street Ride",
             "audio_context_lead_frames": 5},
        ]
        for link in links:
            with self.subTest(link=link):
                document = self.document()
                document["shots"][2].update(link)
                canonical = json.loads(json.dumps(document).replace("Street Ride", "Street_Ride"))
                self.assertEqual(self.normalize(document), self.normalize(canonical))

    def test_numeric_indexes_defaults_and_invalid_links_are_unchanged(self):
        plan = self.normalize(self.document())
        resolve = lambda raw, default=True: chain._resolve_prior_scene_source(
            plan, 3, raw, "context source", default)
        for raw in (1, "1", "Street Ride", "Street_Ride"):
            self.assertEqual(resolve(raw), 1)
        for raw in (None, "", "previous", "immediate"):
            self.assertEqual(resolve(raw), 2)
            self.assertIsNone(resolve(raw, False))
        for raw in (True, "missing scene", "!!!", 0, 3, "End"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                resolve(raw)
        plan["shots"][0]["id"] = "42"
        self.assertEqual(resolve("1"), 1)
        with self.assertRaises(ValueError):
            resolve("42")  # Numeric strings are positions, not IDs.

    def test_colliding_normalized_ids_are_rejected(self):
        document = self.document()
        document["shots"][1]["id"] = "Street_Ride"
        with self.assertRaisesRegex(ValueError, "Duplicate H3 shot id"):
            self.normalize(document)


if __name__ == "__main__":
    unittest.main()
