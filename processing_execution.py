"""Execution provenance stored in immutable processing checkpoint metadata.

The UI workflow and queued API prompt are different documents. Neither may be
replaced by the source generation's recovery archives or a recipe description.
Snapshots live in the existing checkpoint JSON, not new mutable profile files.
"""

import json


def capture(chain, prompt=None, extra_pnginfo=None):
    documents = {}
    workflow = (extra_pnginfo.get("workflow")
                if isinstance(extra_pnginfo, dict) else None)
    for key, value in (("api_prompt", prompt), ("workflow", workflow)):
        if value is None:
            continue
        document = chain._json_document(value)
        if not isinstance(document, dict):
            raise ValueError("Upscale %s metadata must be a JSON object." % key)
        if document:
            documents[key] = document
    return documents


def media_tags(documents):
    return {tag: json.dumps(documents[key], ensure_ascii=False,
                           separators=(",", ":"))
            for key, tag in (("api_prompt", "prompt"), ("workflow", "workflow"))
            if key in documents}


def serialized(documents):
    """Opaque workflow extensions are not strict storage authority fields.

    Some canvas widgets save Infinity/NaN sentinels. Keep their exact JSON
    meaning in strings instead of allowing non-finite control-state numbers.
    """
    return {key:json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            for key,value in documents.items()}


def metadata_fields(documents):
    try:
        json.dumps(documents, allow_nan=False)
    except ValueError:
        return {'execution_json': serialized(documents)}
    return {'execution': documents}


def from_metadata(metadata):
    if 'execution_json' not in metadata:
        return metadata.get('execution')
    if 'execution' in metadata:
        raise ValueError('Processing metadata contains ambiguous execution snapshots.')
    encoded = metadata['execution_json']
    if not isinstance(encoded, dict) or set(encoded)-{'workflow','api_prompt'}:
        raise ValueError('Invalid processing execution snapshot roles.')
    result = {}
    for key, raw in encoded.items():
        if not isinstance(raw, str):
            raise ValueError('Processing execution snapshots require exact JSON strings.')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('Processing execution snapshot is not a JSON object.')
        result[key] = value
    return result


def assembly_tags(chain, segments, *, rehearsal_view=None):
    """Embed each scene's execution, including mixed/resumed and legacy runs.

    Standard ComfyUI tags open the last scene's processing workflow. The additional
    table retains the exact per-scene snapshots, deduplicated within the MP4, so
    settings from a later queue are never silently attributed to earlier scenes.
    Missing legacy provenance stays missing, even when neighbouring scenes have it.
    """
    scenes, documents = [], {}
    selected = {}
    for segment in segments:
        selected = {}
        fingerprint = segment.get("execution_hash")
        if fingerprint:
            try:
                metadata = (rehearsal_view.read(segment.get("revision_metadata"))
                    if rehearsal_view is not None else chain._read_json(chain._absolute_output_path(
                    segment.get("revision_metadata"))))
                if not isinstance(metadata, dict):
                    raise ValueError("processing checkpoint metadata is not an object")
                execution = from_metadata(metadata)
                saved = metadata.get("segment")
                if (not isinstance(saved, dict) or
                        saved.get("revision") != segment.get("revision") or
                        saved.get("index") != segment.get("index") or
                        not isinstance(execution, dict) or
                        chain._fingerprint(execution) != fingerprint):
                    raise ValueError("processing execution identity/hash mismatch")
                selected = execution
                documents[fingerprint] = execution
            except (OSError, TypeError, ValueError) as exc:
                if rehearsal_view is not None:
                    raise
                chain._LOG.warning(
                    "H3 upscale scene %s execution metadata unavailable: %s",
                    segment.get("index"), exc)
        scenes.append({
            "scene": segment.get("index"),
            "revision": segment.get("revision"),
            "execution_hash": fingerprint if selected else None,
        })
    tags = media_tags(selected)
    tags["h3_processing_workflows"] = json.dumps({
        "format": "h3_processing_workflows_v1",
        "standard_tags_scene": scenes[-1]["scene"] if selected else None,
        "scenes": scenes,
        "executions": documents,
    }, ensure_ascii=False, separators=(",", ":"))
    return tags
