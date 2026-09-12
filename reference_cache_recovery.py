"""Rebuild disposable Ref2VA tensors from saved reference identities and media.

Never execute an archived workflow or resolve a tag against today's catalog.
Catalogs are only file indexes: the saved content hash must still match bytes.
"""

import json
from pathlib import Path
import re


class ReferenceRecoveryUnavailable(ValueError):
    pass


def _inside(root, path):
    path = path.resolve()
    if not path.is_relative_to(root.resolve()):
        raise ReferenceRecoveryUnavailable("Saved reference path escapes its archive.")
    return path


def saved_reference_context(chain, source, manifest):
    """Use the generation take, including when the selected source is DeRoPE."""
    source = (source.get("processing_source") or {}).get("original", source)
    run = chain._strict_run_name(manifest.get("run_name"))
    root = Path(chain._output_root()) / "h3_chains" / run
    metadata = {}
    address = source.get("revision_metadata")
    if address:
        path = _inside(root, Path(chain._absolute_output_path(address)))
        if path.is_file():
            metadata = chain._read_json(str(path))
            saved = metadata.get("segment", {})
            if any(saved.get(key) != source.get(key) for key in ("index", "revision", "checkpoint_sha256")):
                raise ReferenceRecoveryUnavailable("Saved source metadata belongs to a different take.")
    compatibility = metadata.get("compatibility") or manifest.get("compatibility") or {}
    dependency = source.get("scene_dependency") or metadata.get("scene_dependency") or {}
    lineage = dependency.get("generation_fingerprint_lineage") or compatibility.get("generation_fingerprint_lineage")
    if not isinstance(lineage, dict):
        raise ReferenceRecoveryUnavailable("This take has no saved reference identities; connect explicit Tagged references.")
    _, lineage = chain._generation_fingerprint_value(json.dumps({"h3_reference_fingerprint_lineage": lineage}))
    if not isinstance(lineage.get("entries"), list):
        raise ReferenceRecoveryUnavailable("This take has no saved reference entry list.")
    return source, root, compatibility, lineage, metadata


def _saved_setting(prompt, value, key=None, seen=frozenset()):
    """Read only known static carriers, never execute an archived node.

    API links are [node ID, output index]. Unknown/dynamic producers, cycles,
    and unsupported output slots are deliberately unresolved.
    """
    if key is None and isinstance(value, str):
        return value
    if (not isinstance(value, list) or len(value) != 2 or value[1] != 0
            or not isinstance(value[0], (str, int))):
        return None
    node_id = str(value[0])
    if node_id in seen or len(seen) >= 64:
        return None
    node = prompt.get(node_id)
    if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
        return None
    inputs, kind = node["inputs"], node.get("class_type")
    seen = seen | {node_id}
    if kind == "Reroute":
        return _saved_setting(prompt, inputs.get("value"), key, seen)
    if key is None and kind in ("PrimitiveString", "PrimitiveStringMultiline"):
        return _saved_setting(prompt, inputs.get("value"), seen=seen)
    if key is not None and kind == "MiniMaxH3TaggedSceneOptions":
        defaults = {"ref_image_size": "match", "semantic_anchor_size": "512",
                    "semantic_anchor_mode": "timestamped_video"}
        return _saved_setting(prompt, inputs.get(key, defaults[key]), seen=seen)
    return None


def _recipe_settings(prompt, defaults):
    if not isinstance(prompt, dict):
        return {}
    candidates = []
    for node in prompt.values():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        inputs, kind = node["inputs"], node.get("class_type")
        if kind in ("MiniMaxH3TaggedReferenceToVideo", "MiniMaxH3ScheduledReferenceToVideo"):
            candidates.append({key: _saved_setting(prompt, inputs.get(key)) for key in defaults})
        elif kind == "MiniMaxH3CurrentTaggedReferenceScene":
            candidates.append(dict(defaults) if "options" not in inputs else {
                key: _saved_setting(prompt, inputs["options"], key) for key in defaults})
    # Unconnected Options nodes are not evidence. Nor is a literal from one
    # conditioner if another conditioner in the same snapshot is unresolved.
    return {key: candidates[0][key] for key in defaults
            if candidates and candidates[0][key] is not None
            and all(item[key] == candidates[0][key] for item in candidates)}


def saved_reference_settings(chain, source, manifest):
    source, root, _compatibility, lineage, metadata = saved_reference_context(chain, source, manifest)
    return _settings(chain, root, source, lineage, metadata)


def _settings(chain, root, source, lineage, metadata, overrides=None):
    overrides = overrides or {}
    settings = {"ref_image_size": "match", "semantic_anchor_size": "512",
                "semantic_anchor_mode": "timestamped_video"}
    recovered = set()
    archives = source.get("archives") or metadata.get("archives") or {}
    address = archives.get("api_prompt")
    # Canonical api_prompt.json is mutable. Only immutable revision snapshots
    # can supply generation settings; never run any node from that document.
    if address:
        path = _inside(root, Path(chain._absolute_output_path(address)))
        archive_root = Path(chain._absolute_output_path(str(root / "recovery_archives")))
        if path.is_relative_to(archive_root) and path.is_file():
            prompt = chain._read_json(str(path))
            recipe = _recipe_settings(prompt, settings)
            settings.update(recipe)
            recovered.update(recipe)
    wrapper = (lineage.get("wrapper") or {}).get("contract") or {}
    if wrapper.get("conditioning_backend", "native_ref2va") != "native_ref2va":
        raise ReferenceRecoveryUnavailable("This take used external RefMod, not native Ref2VA; connect explicit upscale references.")
    for key in settings:
        if key in overrides:
            continue
        values = {item[key] for item in lineage["entries"]
                  if item.get(key) not in (None, "inherit")}
        if key in wrapper:
            values = {wrapper[key]}
        if len(values) > 1:
            raise ReferenceRecoveryUnavailable("Saved references have conflicting %s settings." % key)
        if values:
            settings[key] = values.pop()
            recovered.add(key)
    # A surviving exact cache manifest still records presentation policy even
    # when its tensor bundle/objects have been lost. Do not replace max/1280
    # with defaults just because those tensors need rebuilding.
    descriptor = source.get("reference_cache")
    if isinstance(descriptor, dict):
        path = Path(chain._absolute_output_path(descriptor["metadata"]))
        if path.is_file():
            cached = chain._read_json(str(path))
            if chain._reference_cache_descriptor(cached) == descriptor:
                contract = cached.get("presentation_contract") or {}
                for key in settings:
                    value = cached.get(key) if key == "ref_image_size" else contract.get(key)
                    if value is not None:
                        settings[key] = value
                        recovered.add(key)
    # Keep historical node defaults when an immutable recipe proves them,
    # but preserve native picture detail when the take's policy is unknown.
    # Settings are part of the rebuild identity, so old default-Match caches
    # remain intact and cannot be mistaken for this Max reconstruction.
    if "ref_image_size" not in recovered:
        settings["ref_image_size"] = "max"
    settings.update(overrides)
    recovered.update(overrides)
    if settings["ref_image_size"] not in ("match", "max"):
        raise ReferenceRecoveryUnavailable("Saved ref_image_size is unsupported.")
    chain._semantic_anchor_mode(settings["semantic_anchor_mode"])
    if settings["semantic_anchor_size"] not in chain.SEMANTIC_ANCHOR_SIZES:
        raise ReferenceRecoveryUnavailable("Saved semantic anchor size is unsupported.")
    return settings, sorted(set(settings) - recovered)


def _asset_index(chain, root, run):
    """Index only this project's archives/input copies, not other projects."""
    if __package__:
        from .storage_runtime import current_runtime
        from .storage_project import payload_catalog
    else:
        from storage_runtime import current_runtime
        from storage_project import payload_catalog
    runtime = current_runtime(chain._output_root(), run)
    roots = [root / "project_assets", root / "references",
             Path(chain._input_root()) / "h3_projects" / run]
    records = []
    for base in roots:
        indexed = runtime is not None and base.is_relative_to(root)
        _inside(root if base.is_relative_to(root) else Path(chain._input_root()), base)
        if not indexed and not base.is_dir():
            continue
        for name in ("catalog.json", "manifest.json"):
            path = _inside(base, base / name)
            if indexed:
                path = runtime.reader.path(path)
            if not path.is_file():
                continue
            document = chain._read_json(str(path))

            def collect(value):
                if isinstance(value, dict):
                    if re.fullmatch(r"[0-9a-f]{64}", str(value.get("sha256", ""))) and value.get("relative_path"):
                        relative = Path(value["relative_path"])
                        # RunAssetStore paths include the leading references/.
                        parent = root if name == "manifest.json" else base
                        media = _inside(base, parent / relative)
                        if indexed:
                            media = runtime.reader.path(media)
                        records.append((value["sha256"], media, value))
                    for item in value.values():
                        collect(item)
                elif isinstance(value, list):
                    for item in value:
                        collect(item)
            collect(document)
    if runtime is not None:
        for address, record in payload_catalog(runtime.base).items():
            if address.startswith(('project_assets/', 'references/')):
                records.append((record['file']['sha256'],
                    runtime.reader.path(root/address), {}))
    return roots, records


def _media(chain, roots, records, entry, digest=None, kind=None):
    digest = digest or entry.get("content_hash")
    kind = kind or {"picture": "image", "semantic_anchor": "image"}.get(entry["kind"], entry["kind"])
    if not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
        raise ReferenceRecoveryUnavailable("Saved reference @%s has no content hash." % entry["tag"])
    candidates = [(path, record) for sha, path, record in records if sha == digest]
    # Catalogs may no longer list an old take's media. Archived filenames keep
    # content-addressed prefixes; verify the full hash, never only the prefix.
    for base in roots:
        if base.is_dir():
            for path in base.rglob(digest[:16] + "*"):
                candidates.append((_inside(base, path), {}))
    for path, record in candidates:
        if not path.is_file() or chain._file_sha256(str(path)) != digest:
            continue
        asset = {**record, "kind": kind, "sha256": digest}
        if kind == "video" and not asset.get("metadata"):
            from .project_assets import _probe_media
            asset["metadata"] = _probe_media(str(path), "video")
        return asset, str(path)
    # Older loader-backed Picture refs hashed decoded RGB, while Run Manager
    # archived file bytes. Match both identities before accepting such media;
    # changed crops/resizes must not be approximated by a filename/tag match.
    if kind == "image":
        for sha, path, record in records:
            if (path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
                    or not path.is_file() or chain._file_sha256(str(path)) != sha):
                continue
            asset = {**record, "kind": "image", "sha256": sha}
            value = chain._project_asset_image(chain._project_asset_descriptor(asset, str(path)))
            if chain._tensor_fingerprint(value) == digest:
                return asset, str(path)
    raise ReferenceRecoveryUnavailable(
        "Archived %s @%s (%s) is missing or changed; restore that original file or connect explicit Tagged references."
        % (kind, entry["tag"], digest[:12]))


def _timing_state(chain, root, source, metadata, scene, length):
    address = (source.get("archives") or metadata.get("archives") or {}).get("plan")
    if not address:
        raise ReferenceRecoveryUnavailable("Timed references need this take's saved Plan timing snapshot.")
    path = _inside(root, Path(chain._absolute_output_path(address)))
    archive_root = Path(chain._absolute_output_path(str(root / "recovery_archives")))
    if not path.is_relative_to(archive_root) or not path.is_file():
        raise ReferenceRecoveryUnavailable("Timed references need an immutable Plan snapshot, not today's Plan.")
    plan = chain._read_json(str(path))
    shots = plan.get("shots") or []
    if len(shots) < scene or shots[scene - 1].get("raw_frames") != length:
        raise ReferenceRecoveryUnavailable("Saved reference timing does not match this scene.")
    return {"plan": plan, "index": scene, "source_timeline": metadata.get("source_timeline")}


def recover_reference_cache(chain, source, manifest, scene_count, video_vae, audio_vae,
                            ref_image_size="inherit", semantic_anchor_size="inherit",
                            semantic_anchor_mode="inherit"):
    source, root, compatibility, lineage, metadata = saved_reference_context(chain, source, manifest)
    run = chain._strict_run_name(manifest["run_name"])
    scene, length = int(source["index"]), int(source["raw_frames"])
    prompt = str(source.get("prompt") or "")
    geometry = chain.saved_resolution(source) or compatibility
    width, height = int(geometry["width"]), int(geometry["height"])
    overrides = {}
    for key, value, choices in (
            ("ref_image_size", ref_image_size, ("match", "max")),
            ("semantic_anchor_size", semantic_anchor_size, chain.SEMANTIC_ANCHOR_SIZES),
            ("semantic_anchor_mode", semantic_anchor_mode, chain.SEMANTIC_ANCHOR_MODES)):
        if value not in ("inherit", *choices):
            raise ValueError("Override %s must be one of %s." % (key, ("inherit", *choices)))
        if value != "inherit":
            overrides[key] = value
    settings, defaults = _settings(chain, root, source, lineage, metadata, overrides)
    identity = {"version": 1, "run": run, "source_revision": source.get("revision"),
                "source_checkpoint_sha256": source.get("checkpoint_sha256"), "lineage": lineage,
                "scene": scene, "scene_count": scene_count, "prompt": prompt,
                "width": width, "height": height, "length": length, "settings": settings}
    key = chain._fingerprint(identity)
    # Rebuilt conditioning is disposable, not a new generation checkpoint.
    # On organized projects keep it in the existing content-keyed global cache;
    # Segment Save will adopt any cache actually used by a new generation in
    # that scene's transaction. Never publish an unpinned legacy run directory.
    if __package__:
        from .storage_runtime import current_runtime
    else:
        from storage_runtime import current_runtime
    organized = current_runtime(chain._output_root(), run) is not None
    if organized:
        cached = chain._find_reference_cache(key, scene, scene_count, prompt, width, height, length)
        if cached is not None and not cache_payload_missing(chain, cached):
            cached = chain._load_reference_cache_descriptor(chain._reference_cache_descriptor(cached))
            return cached, "reused references rebuilt from saved media"
    pointer = _inside(root, root / "reference_cache" / ("rebuilt_" + key + ".json"))
    if not organized and pointer.is_file():
        saved = chain._read_json(str(pointer))
        if saved.get("identity") != identity:
            raise ReferenceRecoveryUnavailable("Rebuilt reference cache identity changed.")
        try:
            cached = chain._load_run_reference_cache_descriptor(run, scene, saved["reference_cache"])
            return cached, "reused references rebuilt from saved media"
        except (OSError, ValueError):
            # A derived cache is replaceable, but original media is reverified.
            pass
    tags = chain._prompt_reference_tags(prompt)
    active = [item for item in lineage["entries"]
              if chain._lineage_reference_is_active(item, scene, tags)]
    roots, records = _asset_index(chain, root, run)
    entries, anchors = [], []
    for contract in active:
        entry = dict(contract)
        asset, path = _media(chain, roots, records, entry)
        entry["ranges"] = chain._parse_reference_selector(entry.get("scenes", "all"))
        if entry["kind"] in ("picture", "semantic_anchor"):
            entry["value"] = chain._project_asset_image(chain._project_asset_descriptor(asset, path))
        elif entry["kind"] == "audio":
            entry["value"] = chain._project_asset_descriptor(asset, path)
        elif entry["kind"] == "video":
            paired = bool(entry.get("audio_hash"))
            embedded = paired and entry["audio_hash"] == entry["content_hash"]
            entry["value"] = chain._project_asset_video_descriptor(
                asset, path, embedded_audio=embedded, short_edge=str(entry.get("motion_short_edge") or "source"))
            entry["audio"] = entry["value"] if embedded else None
            if paired and not embedded:
                audio_asset, audio_path = _media(chain, roots, records, entry, entry["audio_hash"], "audio")
                entry["audio"] = chain._project_asset_audio(chain._project_asset_descriptor(audio_asset, audio_path))
        else:
            raise ReferenceRecoveryUnavailable("Cannot rebuild saved reference kind %r." % entry["kind"])
        (anchors if entry["kind"] == "semantic_anchor" else entries).append(entry)
    bundle = chain._make_semantic_anchor_bundle(anchors, settings["semantic_anchor_size"], settings["semantic_anchor_mode"]) if anchors else None
    if lineage.get("registry_mode") == "tagged":
        references = chain._make_tagged_references(entries)
        compiled, _, bindings = chain._compile_tagged_reference_prompt(
            references, scene, scene_count, prompt, "strict", settings["semantic_anchor_mode"], bundle)
    else:
        references = chain._make_reference_schedule(entries)
        bindings = chain._active_reference_bindings(references, scene, scene_count)
        compiled = chain._replace_reference_aliases(prompt, bindings, scene)
    timing = None
    if any(item.get("timeline_mode") in ("sequential", "source_timeline") for item in entries):
        timing = _timing_state(chain, root, source, metadata, scene, length)
    videos, audios = [], []
    for entry in bindings["videos"]:
        video, audio, _ = chain._scheduled_video_reference_slice(entry, timing, scene, scene_count, length)
        if audio is None and entry.get("audio") is not None and not chain._is_lazy_motion_descriptor(entry["audio"]):
            # Lazy video readers only decode embedded audio. Keep separately
            # archived paired sound on the raw reference clock as well.
            audio = entry["audio"]
            if entry.get("timeline_mode") == "sequential":
                shots = timing["plan"]["shots"]
                origin = (next((i for i, shot in enumerate(shots) if
                          set(chain._REFERENCE_ALIAS_RE.findall(shot.get("prompt", ""))).intersection(
                              chain._reference_entry_tags(entry))), scene - 1)
                          if entry.get("activation") == "prompt" else
                          (entry["ranges"][0][0] - 1 if entry["ranges"] else 0))
                start = int(shots[scene - 1]["generation_start_frame"]) - int(shots[origin]["generation_start_frame"])
                audio = chain._slice_audio(audio, start / chain.FPS, length / chain.FPS)
        videos.append({"video": video, "audio": audio})
    for entry in bindings["audios"]:
        audio, _ = chain._tagged_audio_reference_value(entry, timing, scene, scene_count, length)
        audios.append(audio)
    pictures = [entry["value"] for entry in bindings["pictures"]]
    if (pictures or videos) and not callable(getattr(video_vae, "encode", None)):
        raise ReferenceRecoveryUnavailable("Rebuilding visual references needs the H3 video VAE on video_vae.")
    if (audios or any(item["audio"] is not None for item in videos)) and not callable(getattr(audio_vae, "encode", None)):
        raise ReferenceRecoveryUnavailable("Rebuilding audio references needs the H3 audio VAE on audio_vae.")
    presentation = None
    if bindings.get("semantic_anchors"):
        presentation = {"version": chain.SEMANTIC_PRESENTATION_VERSION, "width": width, "height": height,
                        "length": length, **settings, "pictures": pictures,
                        "videos": [{"video": item["video"], "paired_audio": item["audio"] is not None} for item in videos],
                        "standalone_audio_count": len(audios),
                        "anchors": [{"tag": item["tag"], "image": item["entry"]["value"],
                                     "timestamps": tuple(item["timestamps"]), "untimed": bool(item.get("untimed"))}
                                    for item in bindings["semantic_anchors"]]}
    chain._cache_reference_scene(
        fingerprint=key, scene=scene, scene_count=scene_count, prompt=prompt, compiled_prompt=compiled,
        width=width, height=height, length=length, ref_image_size=settings["ref_image_size"],
        vae=video_vae, audio_vae=audio_vae, pictures=pictures, videos=videos, audios=audios,
        semantic_presentation=presentation)
    cached = chain._find_reference_cache(key, scene, scene_count, prompt, width, height, length)
    if not organized:
        cached = chain._adopt_reference_cache_for_run({"run_name": run, "shots": [{}] * scene_count}, cached)
        with chain.checkpoint_run_lock(chain._output_root(), run):
            chain._atomic_json(str(pointer), {"identity": identity, "reference_cache": chain._reference_cache_descriptor(cached)})
    detail = "rebuilt references from verified saved media (no scene regeneration)"
    if defaults:
        detail += "; legacy presentation defaults: " + ", ".join("%s=%s" % (key, settings[key]) for key in defaults)
    return cached, detail


def cache_payload_missing(chain, metadata):
    """Only absence triggers recovery; corrupt existing tensors still fail validation."""
    if metadata.get("format") == chain.REFERENCE_CACHE_FORMAT:
        chain.objects_digest(metadata.get("tensor_objects"))
        paths = [record["tensors"] for record in metadata["tensor_objects"].values()]
    else:
        paths = [metadata["tensors"]]
    return any(not Path(chain._absolute_output_path(path)).is_file() for path in paths)
