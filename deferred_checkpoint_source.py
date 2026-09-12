"""Read a pinned processing branch as a deferred H3 source, without promotion."""

from pathlib import Path
import re

from .checkpoint_variants import processing_lineage, processing_stage, validate_processing_lineage
from .artifact_paths import artifact_address


def editorial_source_manifest(manifest, chain):
    """Freeze the selected final-cut pictures without promoting generation takes."""
    if manifest.get("presentation_source") or manifest.get("processing_source"):
        return manifest  # Already resolved, including a DeRoPE-derived source.
    from .storage_runtime import current_runtime
    runtime = current_runtime(chain._output_root(), manifest["run_name"])
    read_options = {"rehearsal_view": runtime.reader} if runtime else {}
    editorial = chain._manifest_editorial(manifest)
    bases = manifest.get("segments") or []
    # Loop End's ALT manifest already contains the candidate in its segment
    # list. That is a picture selection, not a new original AV checkpoint.
    # Restore its immutable base before resolving presentation; otherwise the
    # stale-cut guard mistakes the candidate for a regenerated base and the
    # upscale reader consumes ALT audio. Never follow today's active pointer.
    for position, candidate in enumerate(bases):
        if candidate.get("take_kind") != "editorial_alternate":
            continue
        scene = int(candidate["index"])
        base_metadata, _ = chain._load_checkpoint_revision(
            manifest["run_name"], scene, candidate.get("alternate_of_revision"),
            **read_options)
        if bases is manifest.get("segments"):
            bases = list(bases)
            editorial = chain._json_document(editorial)
        base = base_metadata["segment"]
        bases[position] = base
        editorial["replacements"] = [item for item in editorial.get("replacements", [])
                                     if int(item["scene"]) != scene] + [{
            "scene": scene, "scene_id": base["id"], "base_revision": base["revision"],
            "alternate_revision": candidate["revision"], "media_mode": "picture_only"}]
    pictures = chain._editorial_presentation_segments(
        manifest["run_name"], bases, editorial)
    output = None
    selected = []
    for position, (base, picture) in enumerate(zip(bases, pictures)):
        if picture.get("presentation_media_mode") != "picture_only":
            continue
        scene = int(base["index"])
        metadata, _path = chain._load_checkpoint_revision(
            manifest["run_name"], scene, picture["revision"], verify_artifacts=False,
            **read_options)
        resolved = dict(picture)
        compatibility = metadata.get("compatibility") or {}
        geometry = chain.saved_resolution(picture) or compatibility
        if geometry.get("width") and geometry.get("height"):
            resolved["resolution"] = {key: int(geometry[key]) for key in ("width", "height")}
        # A different prompt/reference registry belongs to this ALT, not the
        # first base scene's manifest-level conditioning fingerprint.
        resolved["generation_fingerprint"] = str(compatibility.get("generation_fingerprint") or "")
        if isinstance(metadata.get("scene_dependency"), dict):
            resolved["scene_dependency"] = chain._json_document(metadata["scene_dependency"])
        resolved["sample_rate"] = base.get("sample_rate", 0)
        resolved["presentation_source"] = {
            "mode": "picture_only", "original": chain._json_document(base)}
        if output is None:
            output = chain._json_document(manifest)
        output["segments"][position] = resolved
        selected.append({"scene": scene, "base_revision": base["revision"],
                         "alternate_revision": picture["revision"]})
    if not selected:
        return manifest  # Keep existing no-ALT source/resume hashes unchanged.
    output["editorial"] = chain._json_document(editorial)
    output["presentation_source"] = {"format": "h3_deferred_editorial_v1", "scenes": selected}
    return output


def derope_source_manifest(manifest, selection, chain, upscale):
    from .storage_legacy import LegacyStoragePaths
    manifest = editorial_source_manifest(manifest, chain)
    if not isinstance(selection, dict) or selection.get("stage") != "derope":
        raise ValueError("Unknown Checkpoint Manager processing source.")
    from .storage_runtime import current_runtime
    runtime = current_runtime(chain._output_root(), manifest["run_name"])
    root = Path(chain._output_root()).resolve()
    run = Path(runtime.reader.working_directory(manifest["run_name"]) if runtime
               else chain._run_dir(manifest))
    try:
        profile = (root / artifact_address(selection.get("profile_path"))).resolve()
    except ValueError as exc:
        raise ValueError("DeRoPE source profile is outside the selected run.") from exc
    # Exactly run/upscaled/profile or run/chapters/chapter/upscaled/profile.
    relative = profile.relative_to(run).parts if profile.is_relative_to(run) else ()
    if not LegacyStoragePaths.is_processing_profile(relative):
        raise ValueError("DeRoPE source profile is outside the selected run.")

    def confined(address):
        if not isinstance(address, str) or not address:
            raise ValueError("DeRoPE source has a missing artifact address.")
        path = (root / artifact_address(address)).resolve()
        if not path.is_relative_to(profile):
            raise ValueError("DeRoPE source artifact escapes its profile.")
        return path

    branch = selection.get("branch")
    if not isinstance(branch, dict) or not branch.get("lineage"):
        raise ValueError("Select a saved DeRoPE branch, not an isolated clip.")
    def read(path):
        return runtime.reader.read(path) if runtime else chain._read_json(str(path))

    witness = read(confined(branch.get("path")))
    if (not isinstance(witness, dict) or witness.get("run_name") != manifest["run_name"] or
            witness.get("profile") != profile.name):
        raise ValueError("DeRoPE branch belongs to another run/profile.")
    if (branch.get("kind") == "metadata" and
            witness.get("format") == "h3_chain_upscale_segment_v1"):
        saved_lineage = witness.get("processing_lineage")
    elif (branch.get("kind") == "manifest" and witness.get("format") in (
            "h3_chain_upscale_manifest_v1", "h3_chain_upscale_partial_manifest_v1")):
        saved_lineage = processing_lineage(witness.get("segments") or [])
    else:
        raise ValueError("Invalid saved DeRoPE branch format.")
    saved_lineage = validate_processing_lineage(saved_lineage)
    if saved_lineage != validate_processing_lineage(branch["lineage"]):
        raise ValueError("Saved DeRoPE branch changed. Select that processing branch again.")
    by_scene = {int(item["scene"]): item for item in saved_lineage}
    output = chain._json_document(manifest)
    used = []
    for position, original in enumerate(manifest["segments"]):
        index = int(original["index"])
        ref = by_scene.get(index)
        if ref is None:
            continue  # Only an absent scene falls back to its selected original.
        revision = str(ref.get("revision") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", revision):
            raise ValueError("Invalid saved DeRoPE revision.")
        path = confined(ref.get("metadata_path"))
        if path != profile / "checkpoints" / ("clip_%04d.%s.json" % (index, revision)):
            raise ValueError("Saved DeRoPE revision address does not match its scene.")
        metadata = read(path)
        if not isinstance(metadata, dict) or not isinstance(metadata.get("segment"), dict):
            raise ValueError("Saved DeRoPE scene %d has invalid metadata." % index)
        child = metadata.get("segment") or {}
        if (metadata.get("format") != "h3_chain_upscale_segment_v1" or
                metadata.get("run_name") != manifest["run_name"] or
                metadata.get("profile") != profile.name or
                processing_stage(metadata.get("profile_config")) != "derope" or
                child.get("revision") != revision or
                child.get("checkpoint_sha256") != ref.get("checkpoint_sha256")):
            raise ValueError("Saved DeRoPE scene %d identity or stage does not match." % index)
        source_revisions = {original.get("revision"), original.get("adopted_from_revision")}
        if (not child.get("source_revision") or child.get("source_revision") not in source_revisions or
                child.get("source_checkpoint_sha256") != original.get("checkpoint_sha256")):
            raise ValueError("DeRoPE scene %d belongs to a different original take or final-cut ALT. "
                             "Select a DeRoPE branch made from the selected picture, or use Original." % index)
        for key in ("raw_frames", "delivered_frames", "prompt", "prompt_hash", "seed", "steps"):
            if child.get(key) != original.get(key):
                raise ValueError("DeRoPE scene %d has different source timing/settings (%s)." % (index, key))
        if child.get("latent_saved") is not True:
            raise ValueError(
                "DeRoPE scene %d has no full latent (preview/continuation tail only). "
                "Render it with save_latent enabled and Recovered AV connected; "
                "or explicitly select Original. No silent fallback." % index)
        for key in ("checkpoint", "segment", "revision_metadata", "prompt_file", "generated_audio"):
            if child.get(key) is not None:
                confined(child[key])
        upscale._verify_upscale_segment(child, index)
        # Retain the original continuation, timing, prompts, cache descriptor
        # and generation fingerprint. Only media/latent identity comes from HQ.
        resolved = dict(original)
        for key in ("blend_segment", "blend_segment_sha256", "blend_frames",
                    "generated_audio", "generated_audio_sha256", "resolution"):
            resolved.pop(key, None)
        resolved.update(child)
        resolved["processing_source"] = {
            "stage": "derope", "profile_path": str(profile.relative_to(root)),
            "original": chain._json_document(original),
        }
        resolved.pop("presentation_source", None)  # Original owns the ALT/base audio split.
        upscale._validate_processed_latent_header(resolved)
        output["segments"][position] = resolved
        used.append(index)
    if not used:
        raise ValueError("Selected DeRoPE branch has no scenes in this output scope.")
    output["processing_source"] = {"stage": "derope", "scenes": used,
                                   "fallback": "selected_original", "selection": selection}
    # Geometry is checked per scene by deferred readers. A partial combined
    # DeRoPE+upscale branch can legitimately differ from its original fallback.
    return output
