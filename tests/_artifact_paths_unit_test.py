"""Cross-platform address parsing without requiring a Windows host."""

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("artifact_paths", ROOT / "artifact_paths.py")
paths = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paths)


class ArtifactPathTests(unittest.TestCase):
    def test_relative_windows_and_mixed_addresses(self):
        for address in ("h3_chains/demo/clip.json", r"h3_chains\demo\clip.json",
                        "h3_chains/demo\\clip.json"):
            self.assertEqual(paths.artifact_address(address), "h3_chains/demo/clip.json")
        self.assertEqual(paths.artifact_address("h3_chains/Café/clip.json"), "h3_chains/Café/clip.json")

    def test_roots_drives_ads_and_traversal_stay_rejected(self):
        for address in (None, "", ".", "..", "../clip", r"..\clip", r"h3_chains\..\clip",
                        "/clip", r"\clip", r"C:\clip", "C:clip", r"\\host\share\clip",
                        r"\\?\C:\clip", "h3_chains/C:/clip", "clip.json:stream",
                        "h3_chains//demo", "h3_chains/./demo", "h3_chains/demo/", "clip\0json"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                paths.artifact_address(address)


if __name__ == "__main__":
    unittest.main()
