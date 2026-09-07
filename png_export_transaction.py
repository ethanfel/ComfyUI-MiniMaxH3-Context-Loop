"""Recover interrupted PNG publication without touching earlier scene frames.

The journal is written only after a whole scene has been staged and synced.
It grants ownership of exactly the next frame range, never arbitrary PNGs.
All calls are made under the export folder's process lock.
"""

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from . import processing_persistence as persistence

PENDING = ".png_pending.json"
FORMAT = "h3_png_publication_v1"


def _same_record(left, right):
    # A fallback copy changes stat hints, not scene identity or pixel bytes.
    def identity(record):
        record = deepcopy(record)
        if isinstance(record, dict):
            for clip in record.get("clips", []):
                for item in clip.get("files", []):
                    item.pop("mtime_ns", None)
        return record
    return identity(left) == identity(right)


def _cleanup_stage(chain, stage):
    if any(stage.glob("conflict_*")):
        chain._LOG.warning("H3 PNG recovery preserved conflicting interrupted files in %s", stage)
        return
    shutil.rmtree(stage, ignore_errors=True)


@contextmanager
def staging(chain, directory, index):
    stage = Path(tempfile.mkdtemp(prefix=".png_scene_%04d_" % index, dir=directory))
    try:
        yield stage
    finally:
        # Keep staged pixels if publication or its acknowledgement is uncertain.
        # On restart the journal can finish the scene without decoding again.
        try:
            pending = (directory / PENDING).exists()
        except OSError:
            pending = True
        if not pending:
            _cleanup_stage(chain, stage)


def _matches(chain, path, item):
    return path.is_file() and path.stat().st_size == item["size"] and chain._file_sha256(str(path)) == item["sha256"]


def _read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _finish(chain, root, directory, journal, safe_path, publish_frame, recovering=False):
    stage = safe_path(root, directory / journal["stage"])
    record = deepcopy(journal["record"])
    for item in record["clips"][-1]["files"]:
        chain._png_export_check_interrupted()
        target = safe_path(root, directory / item["file"])
        source = safe_path(root, stage / item["file"])
        if recovering and _matches(chain, target, item):
            pass
        else:
            if not _matches(chain, source, item):
                raise ValueError("Interrupted PNG staging is missing or changed: %s. Saved scenes were kept." % source)
            if target.exists():
                if not recovering or not target.is_file():
                    raise FileExistsError("PNG publication will not overwrite %s" % target)
                # A killed network copy can leave a short final file. Preserve
                # any conflicting bytes (including manual edits), then recover
                # from the verified complete staging file. Never delete them.
                preserved = safe_path(root, stage / ("conflict_%s_%s" % (uuid.uuid4().hex, target.name)))
                target.rename(preserved)
                persistence.sync_directory(stage)
                persistence.sync_directory(directory)
                chain._LOG.warning("H3 PNG recovery preserved an interrupted/conflicting file at %s", preserved)
            publish_frame(source, target)
        item["mtime_ns"] = target.stat().st_mtime_ns
    persistence.sync_directory(directory)
    persistence.atomic_json(directory / "export.json", record)
    (directory / PENDING).unlink()
    persistence.sync_directory(directory)
    _cleanup_stage(chain, stage)
    return record


def publish(chain, root, directory, stage, previous, record, safe_path, publish_frame):
    pending = safe_path(root, directory / PENDING)
    if pending.exists():
        raise ValueError("Recover the pending PNG publication before appending another scene.")
    for item in record["clips"][-1]["files"]:
        if safe_path(root, directory / item["file"]).exists():
            raise FileExistsError("PNG publication will not overwrite %s" % item["file"])
    persistence.sync_directory(stage)
    persistence.sync_directory(directory)
    journal = {"format": FORMAT, "stage": stage.name,
               "previous": previous, "record": record}
    persistence.atomic_json(pending, journal)
    try:
        return _finish(chain, root, directory, journal, safe_path, publish_frame)
    except BaseException:
        # Normal cancellation rolls back only verified files from this attempt.
        # An uncertain index commit / unreachable share retains the journal and
        # media. In particular, never undo a successfully replaced export.json.
        try:
            current = _read(safe_path(root, directory / "export.json"))
            if not _same_record(current, record) and _same_record(current, previous):
                for item in record["clips"][-1]["files"]:
                    target = safe_path(root, directory / item["file"])
                    if target.exists():
                        if not _matches(chain, target, item):
                            raise ValueError("Uncertain interrupted PNG publication")
                        target.unlink()
                persistence.sync_directory(directory)
                pending.unlink(missing_ok=True)
                persistence.sync_directory(directory)
        except (OSError, ValueError):
            chain._LOG.warning("H3 PNG publication will be recovered on retry: %s", pending)
        raise


def recover(chain, root, directory, previous, config, contracts, safe_path, publish_frame):
    pending = safe_path(root, directory / PENDING)
    journal = _read(pending)
    if journal is None:
        return previous
    if (not isinstance(journal, dict) or journal.get("format") != FORMAT
            or not re.fullmatch(r"\.png_scene_[0-9]{4,}_[a-zA-Z0-9_-]+", str(journal.get("stage", "")))):
        raise ValueError("Invalid pending PNG journal; existing files were kept.")
    stage = safe_path(root, directory / journal["stage"])
    record, base = journal.get("record"), journal.get("previous")
    if (not isinstance(record, dict) or record.get("format") != "h3_video_png_sequence_v1"
            or record.get("settings") != config or not isinstance(record.get("clips"), list)
            or not record["clips"] or (base is not None and not isinstance(base, dict))):
        raise ValueError("Pending PNG scene belongs to different settings; existing files were kept.")
    old_clips = (base or {}).get("clips", [])
    if not isinstance(old_clips, list):
        raise ValueError("Invalid pending PNG prefix; existing files were kept.")
    clip = record["clips"][-1]
    first = config["first_frame_number"] + sum(c["delivered_frames"] for c in old_clips)
    if (record["clips"][:-1] != old_clips or not isinstance(clip, dict)
            or clip.get("source_contract") != contracts.get(clip.get("index"))
            or (old_clips and clip["index"] != old_clips[-1]["index"] + 1)
            or clip.get("first_frame_number") != first
            or not isinstance(clip.get("files"), list)
            or len(clip["files"]) != clip.get("delivered_frames")
            or not clip["files"]
            or not journal["stage"].startswith(".png_scene_%04d_" % clip["index"])
            or record.get("frame_count") != first + len(clip["files"]) - config["first_frame_number"]):
        raise ValueError("Pending PNG scene has a different branch/order; existing files were kept.")
    for offset, item in enumerate(clip["files"]):
        if (not isinstance(item, dict) or item.get("file") != "frame_%08d.png" % (first + offset)
                or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
                or not isinstance(item.get("size"), int) or item["size"] <= 0):
            raise ValueError("Invalid pending PNG frame address; existing files were kept.")
    if _same_record(previous, record):
        # The index committed before the process stopped; normal validation has
        # already checked every published PNG. Only private staging is obsolete.
        pending.unlink()
        persistence.sync_directory(directory)
        _cleanup_stage(chain, stage)
        return previous
    if not _same_record(previous, base):
        raise ValueError("PNG export changed since the interrupted publication; existing files were kept.")
    result = _finish(chain, root, directory, journal, safe_path, publish_frame, recovering=True)
    chain._LOG.info("H3 PNG recovered committed scene %d from its publication journal", clip["index"])
    return result
