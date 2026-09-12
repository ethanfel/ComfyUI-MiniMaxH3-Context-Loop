"""Numbered PNG sequence destinations, with durable per-upscale-run routing."""

import hashlib
import json
import re

from . import processing_persistence as persistence

MARKER = ".png_variant.json"
FORMAT = "h3_png_variant_v1"


class SequenceConflict(ValueError):
    def __init__(self, message, prefix=None):
        super().__init__(message)
        self.prefix = prefix


def _read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def export(chain, root, base, state, config, safe_path, folder_lock, write):
    # Serialize selection as well as publication. An existing explicit output
    # folder keeps its own lock, including when another exporter names a sibling.
    family = safe_path(root, base / ".png_variants")
    with folder_lock(root, family):
        session = str(state.get("png_export_session") or "")
        binding = None
        selected = None
        if session:
            key = hashlib.sha256(json.dumps([session, config], sort_keys=True).encode()).hexdigest()
            binding = safe_path(root, family / (key + ".json"))
            selection = _read(binding)
            if selection is not None:
                name = selection.get("directory") if isinstance(selection, dict) else None
                if not isinstance(name, str) or not re.fullmatch(re.escape(base.name) + r"(?:_[2-9]|_[1-9][0-9]+)?", name):
                    raise ValueError("Invalid PNG variant binding; saved exports were kept.")
                selected = safe_path(root, base.with_name(name))

        siblings = []
        highest = 1
        for path in base.parent.iterdir():
            match = re.fullmatch(re.escape(base.name) + r"_([1-9][0-9]*)", path.name)
            if match and int(match[1]) >= 2:
                ordinal = int(match[1])
                highest = max(highest, ordinal)
                # Occupied names are never overwritten or followed. Only our
                # own reservation markers make a sibling a reusable sequence.
                if path.is_dir() and not path.is_symlink() and (path / MARKER).is_file():
                    siblings.append((ordinal, path))
        if selected is None:
            selected = max(siblings, default=(1, base))[1]

        while True:
            chain._png_export_check_interrupted()
            selected = safe_path(root, selected)
            try:
                with folder_lock(root, selected):
                    marker = _read(safe_path(root, selected / MARKER))
                    if marker is not None and (not isinstance(marker, dict) or marker.get("format") != FORMAT):
                        raise ValueError("Invalid PNG variant reservation; saved exports were kept.")
                    if marker is not None and marker.get("settings") != config:
                        raise SequenceConflict("PNG variant belongs to different settings.")
                    if binding is not None:
                        # Bind before doing any decoding/publication. A retry
                        # continues this destination even after process exit.
                        selection = {"directory": selected.name}
                        if _read(binding) != selection:
                            persistence.atomic_json(binding, selection)
                    return write(selected, marker)
            except SequenceConflict as exc:
                # Reserve the next free suffix atomically; do not remove any
                # conflicting files, settings, journals, or other exports.
                while True:
                    highest += 1
                    candidate = safe_path(root, base.with_name("%s_%d" % (base.name, highest)))
                    try:
                        candidate.mkdir()
                        break
                    except FileExistsError:
                        continue
                marker = {"format": FORMAT, "settings": config, "prefix": exc.prefix,
                          "reason": str(exc), "first_changed_scene": int(state["index"])}
                persistence.atomic_json(candidate / MARKER, marker)
                chain._LOG.info("H3 PNG preserving %s; using numbered sequence %s: %s", selected, candidate, exc)
                selected = candidate
