"""Durable PNG/take association, independent of node execution order."""

import hashlib
import json

from . import processing_persistence as persistence
from .storage_resolver import logical_output

FORMAT = "h3_png_export_catalog_v1"
# New runtime writes cannot overwrite an immutable imported legacy catalogue.
# Cleanup reads both; reverse recovery keeps the ordinary directory schema.
RUNTIME_CATALOG = "png_sequences/catalog.json"


def owner_key(state, source_contract):
    session = state.get("png_export_session")
    if not session:
        return None  # Legacy/manual callers cannot prove a particular take.
    return hashlib.sha256(json.dumps([
        state["run_name"], state["profile"], state["profile_config"],
        int(state["index"]), source_contract, session,
    ], sort_keys=True).encode()).hexdigest()


def register(root, run_name, directory, safe_path):
    """Caller holds the run lock; catalog supports custom output folders too."""
    path = safe_path(root, root / "h3_chains" / run_name / "png_exports.json")
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "format": FORMAT, "run_name": run_name, "directories": []}
    if (not isinstance(value, dict) or value.get("format") != FORMAT
            or value.get("run_name") != run_name or not isinstance(value.get("directories"), list)
            or not all(isinstance(item, str) for item in value["directories"])):
        raise ValueError("Invalid PNG export catalog; saved exports were kept.")
    address = logical_output(root, directory)
    if address not in value["directories"]:
        value["directories"].append(address)
        value["directories"].sort()
        persistence.atomic_json(path, value)
