#!/usr/bin/env python3
"""Chapter-only manager output: mixed geometry, exact clocks and no writes."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "chapter_output_helpers", ROOT / "tests/_checkpoint_local_output_unit_test.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
chain = h.chain


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        h.h.folder_paths.output_directory = temporary
        run = root / "h3_chains" / "chapter_output"
        metadata, lineage = [], []
        for index in range(1, 5):
            item, _ = h.h.write_revision(
                run, index, str(index) * 32, index, active=True,
                predecessor=metadata[-1] if metadata else None,
                run_name=run.name, context_length=0, audio_context_length=0,
                compatibility={"width":1344 if index < 3 else 960,
                               "height":768 if index < 3 else 544,
                               "audio_mode":"generated_audio"})
            metadata.append(item)
            lineage.append({"scene":index, "revision":str(index) * 32})
        (run / "plan.json").write_text(json.dumps({
            "run_name":run.name, "shots":[{"id":f"scene_{i}"} for i in range(1, 6)]}))
        editorial = {
            "scene_order":[{"scene":i, "scene_id":f"scene_{i}"} for i in range(1, 6)],
            "chapters":[{"id":"one", "start_scene_id":"scene_1"},
                        {"id":"two", "start_scene_id":"scene_3"}],
            "trims":[{"scene_id":"scene_1", "out_frame":294}],
        }
        (run / "editorial.json").write_text(json.dumps(editorial))
        selection = {"run_name":run.name, "output_mode":"workflow_local",
                     "scope_start_scene":3, "scope_end_scene":5, "lineage":lineage}
        h.rejected(lambda: chain._checkpoint_selection_manifest(selection), "compatibility")
        selection["output_scope"] = "chapter"
        chain.claim_project_ownership(temporary, run.name, "another-workflow")
        before = h.snapshot(root)
        with patch.object(chain, "_atomic_json", side_effect=AssertionError("project write")):
            result = chain._checkpoint_selection_manifest(selection)
        assert h.snapshot(root) == before
        with patch.object(chain, "datetime") as clock:
            clock.now.return_value = datetime(2000, 1, 1, tzinfo=timezone.utc)
            first_read = chain._checkpoint_selection_manifest(selection)
            clock.now.return_value = datetime(2040, 1, 1, tzinfo=timezone.utc)
            assert chain._checkpoint_selection_manifest(selection) == first_read == result, \
                "legacy sidecar reads must not change source hashes with wall-clock time"
        assert [s["index"] for s in result["segments"]] == [3, 4]
        assert result["format"] == chain.CHAPTER_MANIFEST_FORMAT
        assert result["compatibility"]["width"] == 960
        assert result["chapter"]["number"] == 2
        assert result["chapter"]["complete"] is False
        assert result["source_scene_count"] == 5
        assert result["chapter"]["source_start_frame"] == 702
        assert result["chapter"]["editorial_origin_frame"] == 634
        assert result["editorial"]["placements"][0]["start_frame"] == 0
        assert "chapter_manifest_path" not in result and not (run / "chapters").exists()
        selected_metadata = root / metadata[3]["segment"]["revision_metadata"]
        original_metadata = selected_metadata.read_text()
        incompatible = json.loads(original_metadata)
        incompatible["compatibility"]["width"] = 1024
        selected_metadata.write_text(json.dumps(incompatible))
        h.rejected(lambda: chain._checkpoint_selection_manifest(selection),
                   "compatible takes within this chapter")
        try:
            chain._checkpoint_selection_manifest(selection)
        except ValueError as exc:
            message = str(exc)
            assert "scene 3 revision " + "3" * 32 in message, message
            assert "scene 4 revision " + "4" * 32 in message, message
            assert "width: scene 3 = 960; scene 4 = 1024" in message, message
            assert "scene 1 revision" not in message, message
        else:
            raise AssertionError("Chapter geometry mismatch was accepted")
        selected_metadata.write_text(original_metadata)

        # The reported Chapter 2 takes differ only in reference-catalog
        # fingerprints. This is not a geometry or saved-lineage mismatch.
        changed_catalog = json.loads(original_metadata)
        changed_catalog["compatibility"].update({
            "generation_fingerprint":"new-catalog",
            "generation_fingerprint_lineage":{"current":"new-catalog"}})
        selected_metadata.write_text(json.dumps(changed_catalog))
        mixed = chain._checkpoint_selection_manifest(selection)
        assert [s["generation_fingerprint"] for s in mixed["segments"]] == ["", "new-catalog"]
        selected_metadata.write_text(original_metadata)

        # Appending empty planned scenes after this take was generated must
        # not prevent selecting it. Reference schedules retain the archived
        # Plan's count, while the editorial chapter may extend beyond it.
        plan_path = run / "plan.json"
        plan_before = plan_path.read_text()
        plan_path.write_text(json.dumps({
            "run_name":run.name, "shots":[{"id":f"scene_{i}"} for i in range(1, 5)]}))
        older = chain._checkpoint_selection_manifest(selection)
        assert older["source_scene_count"] == 4
        assert older["chapter"]["planned_end_scene"] == 5
        plan_path.write_text(plan_before)

        # UI scope ends at the current scene order; immutable archived Plans
        # can include more empty scenes (real regression: UI 12, archive 13).
        for planned_count, ui_end in ((6, 5), (5, 4), (5, 7)):
            plan_path.write_text(json.dumps({
                "run_name":run.name,
                "shots":[{"id":f"scene_{i}"} for i in range(1, planned_count + 1)]}))
            pinned = chain._checkpoint_selection_manifest({
                **selection, "scope_end_scene":ui_end})
            assert [s["revision"] for s in pinned["segments"]] == ["3" * 32, "4" * 32]
            assert pinned["chapter"]["start_scene"] == 3
            assert pinned["chapter"]["planned_end_scene"] == max(5, planned_count)
        plan_path.write_text(plan_before)

        # A real boundary crossing within the pinned takes is not an empty
        # tail difference: this must still require an explicit new selection.
        (run / "editorial.json").write_text(json.dumps({
            **editorial, "chapters":[*editorial["chapters"],
                {"id":"three", "start_scene_id":"scene_4"}]}))
        h.rejected(lambda: chain._checkpoint_selection_manifest(selection), "boundaries")
        (run / "editorial.json").write_text(json.dumps(editorial))

        # A one-scene selection without explicit chapter markers is Chapter 1
        # even when its archived Plan includes more ungenerated shots.
        (run / "editorial.json").write_text("{}")
        implicit = chain._checkpoint_selection_manifest({
            **selection, "lineage":lineage[:1],
            "scope_start_scene":1, "scope_end_scene":1})
        assert [s["index"] for s in implicit["segments"]] == [1]
        (run / "editorial.json").write_text(json.dumps(editorial))

        # Earlier media is not an upscale input. Retain its immutable timing
        # metadata, but remove the fixture's video and latent artifacts.
        for item in metadata[:2]:
            for key in ("segment", "checkpoint"):
                (root / item["segment"][key]).unlink()
        before = h.snapshot(root)
        assert chain._checkpoint_selection_manifest(selection) == result
        assert h.snapshot(root) == before
        h.rejected(lambda: chain._checkpoint_selection_manifest({
            **selection, "scope_start_scene":4}), "boundaries")
        h.rejected(lambda: chain._checkpoint_selection_manifest({
            **selection, "output_scope":"invalid"}), "unknown output scope")
        # A wrong take within the selected chapter is still rejected.
        other, _ = h.h.write_revision(run, 3, "a" * 32, 10, run_name=run.name,
            compatibility=metadata[2]["compatibility"], context_length=0, audio_context_length=0)
        h.rejected(lambda: chain._checkpoint_selection_manifest({
            **selection, "lineage":[*lineage[:2], {"scene":3,"revision":"a" * 32}, lineage[3]]}),
            "depends on")
        (root / metadata[3]["segment"]["segment"]).write_bytes(b"corrupt")
        h.rejected(lambda: chain._checkpoint_selection_manifest(selection), "integrity")
    print("Checkpoint chapter output: mixed geometry, original indexes/clocks, unavailable prior media, read-only and fail-closed checks pass")


if __name__ == "__main__":
    main()
