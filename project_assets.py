"""Project-owned media catalog for the H3 Asset Carousel node.

The catalog lives below ComfyUI's input directory so ordinary loader nodes can
open every imported file without this pack.  A second, content-identical copy
is retained below the matching H3 chain output directory for recovery.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from fractions import Fraction
from functools import wraps
from typing import Any

if __package__:
    from .audio_track_contract import audio_track_bindings
else:  # Standalone catalog tools and storage tests.
    from audio_track_contract import audio_track_bindings

try:
    import av
except ImportError:  # ComfyUI normally includes PyAV.
    av = None

try:
    from PIL import Image, ImageOps
except ImportError:  # ComfyUI normally includes Pillow.
    Image = ImageOps = None


PROJECT_ASSET_FORMAT = "h3_project_assets_v1"
PROJECT_ASSET_VERSION = 1
PROJECT_ASSET_ROLES = (
    "picture", "semantic_anchor", "video", "motion",
    "audio_reference", "source_track",
)
IMAGE_EXTENSIONS = frozenset((
    ".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif",
    ".tiff", ".webp",
))
VIDEO_EXTENSIONS = frozenset((
    ".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg",
    ".webm",
))
AUDIO_EXTENSIONS = frozenset((
    ".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus",
    ".wav",
))
ALL_MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
MAX_CATALOG_ASSETS = 512
MAX_REFERENCE_SLOTS = 512
MAX_ASSET_FOLDERS = 128
MAX_ASSET_LYRICS_CHARACTERS = 100_000
MAX_DERIVED_IMAGE_PIXELS = 268_435_456
MAX_INPUT_RESULTS = 2000
INPUT_BROWSER_EXCLUDED_DIRECTORIES = frozenset((
    "clipspace", "h3_projects",
))
_TAG_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_PROJECT_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,94}[A-Za-z0-9])?")
_CATALOG_LOCKS: dict[str, threading.RLock] = {}
_CATALOG_LOCKS_GUARD = threading.Lock()


class ProjectAssetConflictError(ValueError):
    """The catalog changed after a caller loaded its editing snapshot."""


_LOG = logging.getLogger("minimax_h3_context_loop.project_assets")


def _project_mutation(method):
    """Serialize catalog read/modify/write across request-local store instances.

    Reentrant because slot binding and derived imports call other mutations.
    Media extraction happens before import and does not hold this lock.
    """
    @wraps(method)
    def locked(self, project, *args, **kwargs):
        directory, _name = self._project_dir(project)
        # Share nightly's existing lock and retain its cross-process CAS
        # checks, instead of adding a second, independent locking system.
        with _catalog_lock(os.path.join(directory, "catalog.json")):
            return method(self, project, *args, **kwargs)
    return locked


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def _safe_name(value: Any, fallback: str = "asset", limit: int = 128) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    text = text.strip("._-")
    return (text or fallback)[:limit]


def _safe_project(value: Any) -> str:
    requested = str(value or "").strip()
    project = _safe_name(requested, "", 96)
    if not requested or not project:
        raise ValueError("Run name cannot be blank.")
    if requested != project or _PROJECT_RE.fullmatch(requested) is None:
        raise ValueError(
            "Run name must be 1-96 characters, start and end with a letter "
            "or number, and contain only letters, numbers, '.', '_' or '-'. "
            "Use %r for this project." % project)
    return project


def _catalog_lock(path: str) -> threading.RLock:
    normalized = os.path.realpath(path)
    with _CATALOG_LOCKS_GUARD:
        return _CATALOG_LOCKS.setdefault(normalized, threading.RLock())


@contextmanager
def _catalog_file_lock(path: str):
    """Serialize catalog commits across ComfyUI processes sharing storage."""
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _safe_tag(value: Any, fallback: str = "asset") -> str:
    tag = str(value or "").strip().lstrip("@#")
    if not tag:
        tag = _safe_name(fallback, "asset", 64)
        if not tag[:1].isalpha():
            tag = "asset_" + tag
    if _TAG_RE.fullmatch(tag) is None:
        tag = re.sub(r"[^A-Za-z0-9_-]+", "_", tag).strip("_-")[:64]
        if not tag or not tag[0].isalpha():
            tag = "asset_" + (tag or uuid.uuid4().hex[:8])
        tag = tag[:64]
    if _TAG_RE.fullmatch(tag) is None:
        raise ValueError("Asset tag must begin with a letter and contain only letters, numbers, _ or -.")
    return tag


def _safe_folder_name(value: Any) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        raise ValueError("Asset folder name cannot be blank.")
    return name[:80]


def _unique_tag(catalog: dict[str, Any], value: Any, fallback: str,
                *, exclude_id: str = "") -> str:
    tag = _safe_tag(value, fallback)
    used = {
        str(item.get("tag") or "") for item in catalog.get("assets", [])
        if str(item.get("id") or "") != exclude_id
        and bool(item.get("enabled", True))
    }
    if tag not in used:
        return tag
    base = tag
    ordinal = 2
    while tag in used:
        suffix = "_%d" % ordinal
        tag = base[:64 - len(suffix)] + suffix
        ordinal += 1
    return tag


_TRAILING_DIGITS_RE = re.compile(r"^(.*?)(\d+)$")


def _capture_family_tag(catalog: dict[str, Any], value: Any, fallback: str) -> str:
    """Uniquify a captured-frame tag as an updated take of its family.

    Unlike _unique_tag (which appends "_2", "_3", ... on any collision),
    a tag reused for a video-frame capture is meant to read as an updated
    take of the same subject: @char-sammy -> @char-sammy1, then
    @char-sammy2, and so on, with no underscore before the number.
    """
    tag = _safe_tag(value, fallback)
    used = {
        str(item.get("tag") or "") for item in catalog.get("assets", [])
    }
    if tag not in used:
        return tag
    match = _TRAILING_DIGITS_RE.match(tag)
    stem = match.group(1) if match else tag
    highest = 0
    for other in used:
        if other == stem or not other.startswith(stem):
            continue
        suffix = other[len(stem):]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    ordinal = highest + 1
    suffix = str(ordinal)
    candidate = stem[:64 - len(suffix)] + suffix
    while candidate in used:
        ordinal += 1
        suffix = str(ordinal)
        candidate = stem[:64 - len(suffix)] + suffix
    return candidate


def _image_resampling(value: Any):
    if Image is None:
        raise RuntimeError("Pillow is required for project image variants.")
    name = str(value or "lanczos").strip().lower()
    modes = {
        "nearest": Image.Resampling.NEAREST,
        "box": Image.Resampling.BOX,
        "bilinear": Image.Resampling.BILINEAR,
        "hamming": Image.Resampling.HAMMING,
        "bicubic": Image.Resampling.BICUBIC,
        "lanczos": Image.Resampling.LANCZOS,
    }
    if name not in modes:
        raise ValueError("Image resampling must be nearest, box, bilinear, hamming, bicubic, or lanczos.")
    return name, modes[name]


def _image_operation_geometry(image: Any, crop: Any, target: Any) -> tuple[int, int, int, int, int, int]:
    if not isinstance(crop, dict):
        raise ValueError("Image crop must be a JSON object.")
    if not isinstance(target, dict):
        raise ValueError("Image target size must be a JSON object.")
    try:
        x = int(crop.get("x", 0))
        y = int(crop.get("y", 0))
        width = int(crop.get("width", image.width))
        height = int(crop.get("height", image.height))
        target_width = int(target.get("width", width))
        target_height = int(target.get("height", height))
    except (TypeError, ValueError) as exc:
        raise ValueError("Crop and target dimensions must be whole pixels.") from exc
    if x < 0 or y < 0 or width < 1 or height < 1:
        raise ValueError("Crop dimensions must describe a positive area inside the source image.")
    if x + width > image.width or y + height > image.height:
        raise ValueError(
            "Crop %d,%d %dx%d exceeds the oriented source image %dx%d." %
            (x, y, width, height, image.width, image.height))
    if target_width < 1 or target_height < 1:
        raise ValueError("Target width and height must be positive.")
    if target_width * target_height > MAX_DERIVED_IMAGE_PIXELS:
        raise ValueError("Derived image target exceeds %d pixels." % MAX_DERIVED_IMAGE_PIXELS)
    return x, y, width, height, target_width, target_height


def _inside(root: str, path: str) -> bool:
    try:
        return os.path.commonpath(
            (os.path.realpath(root), os.path.realpath(path))) == os.path.realpath(root)
    except ValueError:
        return False


def _atomic_json(path: str, value: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = "%s.%s.tmp" % (path, uuid.uuid4().hex)
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory = os.open(os.path.dirname(path), os.O_RDONLY)
        except OSError:
            directory = None
        if directory is not None:
            try:
                os.fsync(directory)
            except OSError:
                pass
            finally:
                os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_fingerprint(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _media_kind(path: str) -> str:
    suffix = os.path.splitext(path)[1].lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    raise ValueError("Unsupported media extension %r." % suffix)


def _role_for_kind(kind: str, role: Any) -> str:
    role = str(role or "").strip().lower()
    defaults = {"image": "picture", "video": "video", "audio": "audio_reference"}
    role = role or defaults[kind]
    allowed = {
        "image": {"picture", "semantic_anchor"},
        "video": {"video", "motion", "source_track"},
        "audio": {"audio_reference", "source_track"},
    }
    if role not in allowed[kind]:
        raise ValueError("Role %r is not valid for a %s asset." % (role, kind))
    return role


def _stream_duration(stream: Any, container: Any) -> float:
    if stream.duration is not None and stream.time_base is not None:
        return max(0.0, float(stream.duration * stream.time_base))
    if container.duration is not None:
        return max(0.0, float(container.duration) / 1_000_000.0)
    return 0.0


def _fraction_parts(value: Any, fallback: int = 24) -> tuple[int, int]:
    try:
        rate = Fraction(value)
    except (TypeError, ValueError, ZeroDivisionError):
        rate = Fraction(fallback, 1)
    if rate <= 0:
        rate = Fraction(fallback, 1)
    return int(rate.numerator), int(rate.denominator)


def _probe_media(path: str, kind: str) -> dict[str, Any]:
    if kind == "image":
        if Image is None:
            return {}
        with Image.open(path) as image:
            return {
                "width": int(image.width),
                "height": int(image.height),
                "format": str(image.format or "").lower(),
                "frames": int(getattr(image, "n_frames", 1)),
            }
    if av is None:
        return {}
    with av.open(path, mode="r") as container:
        if kind == "video":
            videos = list(container.streams.video)
            if not videos:
                raise ValueError("Video asset contains no video stream.")
            video = videos[0]
            rate = video.average_rate or video.guessed_rate or video.base_rate
            numerator, denominator = _fraction_parts(rate)
            duration = _stream_duration(video, container)
            frames = int(video.frames or 0)
            if frames <= 0 and duration > 0:
                frames = int(round(duration * numerator / denominator))
            audio_metadata = {}
            audios = list(container.streams.audio)
            if audios:
                audio = audios[0]
                audio_metadata = {
                    "audio_sample_rate": int(
                        audio.codec_context.sample_rate or audio.rate or 0),
                    "audio_channels": len(audio.codec_context.layout.channels),
                    "audio_start_seconds": float(
                        audio.start_time * audio.time_base)
                        if audio.start_time is not None
                        and audio.time_base is not None else 0.0,
                }
            return {
                "width": int(video.codec_context.width or video.width or 0),
                "height": int(video.codec_context.height or video.height or 0),
                "duration_seconds": duration,
                "frame_count": frames,
                "source_rate_numerator": numerator,
                "source_rate_denominator": denominator,
                "fps": numerator / float(denominator),
                "codec": str(video.codec_context.name or ""),
                "video_start_seconds": float(
                    video.start_time * video.time_base)
                    if video.start_time is not None and video.time_base is not None
                    else 0.0,
                "has_audio": bool(audios),
                **audio_metadata,
            }
        audios = list(container.streams.audio)
        if not audios:
            raise ValueError("Audio asset contains no audio stream.")
        audio = audios[0]
        sample_rate = int(audio.codec_context.sample_rate or audio.rate or 0)
        channels = len(audio.codec_context.layout.channels)
        return {
            "duration_seconds": _stream_duration(audio, container),
            "sample_rate": sample_rate,
            "channels": channels,
            "codec": str(audio.codec_context.name or ""),
        }


def _entry_contract(entry: dict[str, Any]) -> dict[str, Any]:
    options = entry.get("options") if isinstance(entry.get("options"), dict) else {}
    return {
        "id": str(entry.get("id") or ""),
        "kind": str(entry.get("kind") or ""),
        "role": str(entry.get("role") or ""),
        "tag": str(entry.get("tag") or ""),
        "sha256": str(entry.get("sha256") or ""),
        "enabled": bool(entry.get("enabled", True)),
        "options": options,
    }


def _slot_contract(slot: dict[str, Any]) -> dict[str, Any]:
    options = slot.get("options") if isinstance(slot.get("options"), dict) else {}
    return {
        "id": str(slot.get("id") or ""),
        "kind": str(slot.get("kind") or ""),
        "role": str(slot.get("role") or ""),
        "tag": str(slot.get("tag") or ""),
        "content_hash": str(slot.get("content_hash") or ""),
        "available": bool(slot.get("available", True)),
        "options": options,
    }


class ProjectAssetStore:
    """Own project media below input/h3_projects and mirror it to a run."""

    def __init__(self, input_root: str, output_root: str):
        self.input_root = os.path.realpath(os.path.abspath(input_root))
        self.output_root = os.path.realpath(os.path.abspath(output_root))
        self.projects_root = os.path.join(self.input_root, "h3_projects")
        self.chains_root = os.path.join(self.output_root, "h3_chains")

    def _project_dir(self, project: Any) -> tuple[str, str]:
        name = _safe_project(project)
        path = os.path.realpath(os.path.join(self.projects_root, name))
        if not _inside(self.input_root, path):
            raise ValueError("Project asset path escapes the ComfyUI input directory.")
        return path, name

    def _backup_dir(self, project: Any) -> tuple[str, str]:
        name = _safe_project(project)
        path = os.path.realpath(os.path.join(
            self.chains_root, name, "project_assets"))
        if not _inside(self.output_root, path):
            raise ValueError("Project backup path escapes the ComfyUI output directory.")
        return path, name

    @staticmethod
    def _empty_catalog(project: str) -> dict[str, Any]:
        return {
            "format": PROJECT_ASSET_FORMAT,
            "version": PROJECT_ASSET_VERSION,
            "project": project,
            "updated_at": _utc_now(),
            "revision": "",
            "assets": [],
            "reference_slots": [],
            "folders": [],
        }

    @_project_mutation
    def load(self, project: Any, *, create: bool = False) -> dict[str, Any]:
        directory, name = self._project_dir(project)
        path = os.path.join(directory, "catalog.json")
        backup_path = os.path.join(self._backup_dir(name)[0], "catalog.json")
        catalog = None
        primary_error = None
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    catalog = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                primary_error = exc
        if catalog is None and os.path.isfile(backup_path):
            # A writer may repair or replace the primary between our first
            # read and recovery. Re-check it while holding the same process
            # and filesystem locks used by catalog commits so an older mirror
            # can never overwrite that newer accepted state.
            with _catalog_lock(path), _catalog_file_lock(path):
                if os.path.isfile(path):
                    try:
                        with open(path, "r", encoding="utf-8") as handle:
                            catalog = json.load(handle)
                        primary_error = None
                    except (OSError, json.JSONDecodeError) as exc:
                        primary_error = exc
                if catalog is None:
                    try:
                        with open(backup_path, "r", encoding="utf-8") as handle:
                            catalog = json.load(handle)
                        os.makedirs(directory, exist_ok=True)
                        _atomic_json(path, catalog)
                    except (OSError, json.JSONDecodeError):
                        if primary_error is not None:
                            raise primary_error
                        catalog = None
        if catalog is None:
            if primary_error is not None:
                raise primary_error
            catalog = self._empty_catalog(name)
            if create:
                return self._save_catalog(catalog)
            return catalog
        if (not isinstance(catalog, dict)
                or catalog.get("format") != PROJECT_ASSET_FORMAT
                or int(catalog.get("version", -1)) != PROJECT_ASSET_VERSION
                or not isinstance(catalog.get("assets"), list)):
            raise ValueError("Project asset catalog has an unsupported format.")
        catalog["project"] = name
        slots = catalog.get("reference_slots")
        catalog["reference_slots"] = (
            slots if isinstance(slots, list) else [])
        folders = catalog.get("folders")
        catalog["folders"] = folders if isinstance(folders, list) else []
        storage_revision = str(catalog.get("storage_revision") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", storage_revision):
            storage_revision = _canonical_fingerprint({
                key: value for key, value in catalog.items()
                if key != "storage_revision"
            })[:32]
        catalog["storage_revision"] = storage_revision
        return catalog

    def _save_catalog(self, catalog: dict[str, Any]) -> dict[str, Any]:
        directory, name = self._project_dir(catalog.get("project"))
        path = os.path.join(directory, "catalog.json")
        expected_storage_revision = str(
            catalog.get("storage_revision") or "")
        assets = [dict(item) for item in catalog.get("assets", [])
                  if isinstance(item, dict)]
        slots = [dict(item) for item in catalog.get("reference_slots", [])
                 if isinstance(item, dict)]
        folders = [dict(item) for item in catalog.get("folders", [])
                   if isinstance(item, dict)]
        if len(assets) > MAX_CATALOG_ASSETS:
            raise ValueError("Project asset catalog supports at most %d assets." % MAX_CATALOG_ASSETS)
        if len(slots) > MAX_REFERENCE_SLOTS:
            raise ValueError(
                "Project asset catalog supports at most %d unresolved "
                "reference slots." % MAX_REFERENCE_SLOTS)
        if len(folders) > MAX_ASSET_FOLDERS:
            raise ValueError(
                "Project asset catalog supports at most %d folders." %
                MAX_ASSET_FOLDERS)
        folder_ids = [str(item.get("id") or "") for item in folders]
        if (any(not item for item in folder_ids)
                or len(folder_ids) != len(set(folder_ids))):
            raise ValueError("Every asset folder must have a unique ID.")
        known_folders = set(folder_ids)
        for asset in assets:
            options = asset.get("options") or {}
            if options.get("audio_tracks") is not None:
                if asset.get("kind") != "audio":
                    raise ValueError("Only audio assets can group audio tracks.")
                asset["options"] = {
                    **options, "audio_tracks": audio_track_bindings(
                        options["audio_tracks"], assets),
                }
            folder_id = str(asset.get("folder_id") or "")
            if folder_id and folder_id not in known_folders:
                asset["folder_id"] = ""
        document = {
            "format": PROJECT_ASSET_FORMAT,
            "version": PROJECT_ASSET_VERSION,
            "project": name,
            "updated_at": _utc_now(),
            "assets": assets,
            "reference_slots": slots,
            "folders": folders,
        }
        document["revision"] = _canonical_fingerprint({
            "assets": [_entry_contract(item) for item in assets],
            "reference_slots": [_slot_contract(item) for item in slots],
        })
        document["storage_revision"] = uuid.uuid4().hex
        with _catalog_lock(path), _catalog_file_lock(path):
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as handle:
                    current = json.load(handle)
                current_storage_revision = str(
                    current.get("storage_revision") or "")
                if not re.fullmatch(
                        r"[0-9a-f]{32}", current_storage_revision):
                    current_storage_revision = _canonical_fingerprint({
                        key: value for key, value in current.items()
                        if key != "storage_revision"
                    })[:32]
                if (not expected_storage_revision
                        and not assets and not slots and not folders):
                    current["storage_revision"] = current_storage_revision
                    return current
                if expected_storage_revision != current_storage_revision:
                    raise ProjectAssetConflictError(
                        "Project assets changed in another request. Refresh "
                        "the Carousel and repeat this edit; no catalog data "
                        "was overwritten.")
            elif expected_storage_revision:
                raise ProjectAssetConflictError(
                    "Project assets were removed or replaced after they were "
                    "loaded. Refresh the Carousel before editing.")
            for group in ("images", "videos", "audio", "previews", ".uploads"):
                os.makedirs(os.path.join(directory, group), exist_ok=True)
            backup, _name = self._backup_dir(name)
            os.makedirs(backup, exist_ok=True)
            # The input-side catalog is authoritative and atomically durable.
            # Publish it first so an interrupted mirror refresh can never make
            # a speculative catalog look newer than the accepted project.
            _atomic_json(path, document)
            try:
                _atomic_json(os.path.join(backup, "catalog.json"), document)
            except OSError as exc:
                _LOG.warning(
                    "Project %s catalog committed, but its output recovery "
                    "mirror could not be refreshed: %s", name, exc)
        return document

    def public_catalog(self, project: Any, *, create: bool = True) -> dict[str, Any]:
        catalog = self.load(project, create=create)
        return {
            **catalog,
            "assets": [dict(item) for item in catalog["assets"]],
            "reference_slots": [
                dict(item) for item in catalog.get("reference_slots", [])],
            "folders": [dict(item) for item in catalog.get("folders", [])],
        }

    def duplicate_project(
            self, project: Any, new_project: Any) -> dict[str, Any]:
        """Clone one complete asset project without copying generated output.

        Only catalog-referenced media below ``input/h3_projects`` and its
        ``project_assets`` recovery mirror are copied.  The source run's
        checkpoints, generated clips, assembled videos, and other output
        files are never traversed.
        """
        source_directory, source_name = self._project_dir(project)
        target_directory, target_name = self._project_dir(new_project)
        if source_name == target_name:
            raise ValueError(
                "The duplicated asset project needs a different Run name.")
        source_catalog_path = os.path.join(source_directory, "catalog.json")
        if not os.path.isfile(source_catalog_path):
            raise FileNotFoundError(
                "Asset project %s does not exist." % source_name)

        source_catalog = self.load(source_name)
        media: dict[str, tuple[str, str, int]] = {}
        for entry in source_catalog.get("assets", []):
            source_path = self._asset_path(source_name, entry)
            relative = str(entry.get("relative_path") or "").replace(
                "/", os.sep)
            normalized = os.path.normpath(relative)
            if (not relative or normalized in ("", ".")
                    or os.path.isabs(normalized)
                    or normalized == os.pardir
                    or normalized.startswith(os.pardir + os.sep)):
                raise ValueError(
                    "Project asset media path escapes its store: %s" %
                    relative)
            digest = _file_sha256(source_path)
            expected = str(entry.get("sha256") or "")
            if expected and digest != expected:
                raise ValueError(
                    "Project asset %s no longer matches its catalog SHA-256."
                    % entry.get("tag", entry.get("id", "asset")))
            existing = media.get(normalized)
            if existing is not None and existing[1] != digest:
                raise ValueError(
                    "Project asset catalog maps one media path to different "
                    "content.")
            media[normalized] = (
                source_path, digest, int(os.path.getsize(source_path)))

        target_backup, _name = self._backup_dir(target_name)
        target_run_directory = os.path.dirname(target_backup)
        if os.path.exists(target_directory):
            raise FileExistsError(
                "An asset project named %s already exists. Choose a new Run "
                "name." % target_name)
        if os.path.exists(target_run_directory):
            raise FileExistsError(
                "An H3 output Run named %s already exists. Choose a new Run "
                "name so its renders and checkpoints remain separate." %
                target_name)

        os.makedirs(self.projects_root, exist_ok=True)
        os.makedirs(self.chains_root, exist_ok=True)
        created_project = False
        created_run = False
        try:
            os.mkdir(target_directory)
            created_project = True
            os.mkdir(target_run_directory)
            created_run = True
            os.makedirs(target_backup)
            for group in ("images", "videos", "audio", "previews", ".uploads"):
                os.makedirs(os.path.join(target_directory, group))
            for group in ("images", "videos", "audio"):
                os.makedirs(os.path.join(target_backup, group))

            for relative, (source_path, digest, _size) in media.items():
                for root in (target_directory, target_backup):
                    destination = os.path.realpath(os.path.join(root, relative))
                    if not _inside(root, destination):
                        raise ValueError(
                            "Project asset media path escapes its duplicate.")
                    os.makedirs(os.path.dirname(destination), exist_ok=True)
                    shutil.copy2(source_path, destination)
                    if _file_sha256(destination) != digest:
                        raise ValueError(
                            "Duplicated project asset failed its SHA-256 check.")

            now = _utc_now()
            cloned_catalog = copy.deepcopy(source_catalog)
            cloned_catalog["project"] = target_name
            cloned_catalog["updated_at"] = now
            # The source token protects only the source catalog. The target is
            # a new independent document and therefore starts without a base.
            cloned_catalog["storage_revision"] = ""
            for entry in cloned_catalog.get("assets", []):
                relative = str(entry.get("relative_path") or "")
                entry["input_path"] = os.path.join(
                    "h3_projects", target_name, relative).replace(os.sep, "/")
                entry["updated_at"] = now
            saved = self._save_catalog(cloned_catalog)
            return {
                "catalog": saved,
                "source_project": source_name,
                "target_project": target_name,
                "asset_count": len(saved.get("assets", [])),
                "media_file_count": len(media),
                "copied_bytes": sum(item[2] for item in media.values()),
                "render_outputs_copied": False,
            }
        except Exception:
            if created_run:
                shutil.rmtree(target_run_directory, ignore_errors=True)
            if created_project:
                shutil.rmtree(target_directory, ignore_errors=True)
            raise

    def _asset_path(self, project: Any, entry: dict[str, Any]) -> str:
        directory, _name = self._project_dir(project)
        relative = str(entry.get("relative_path") or "")
        path = os.path.realpath(os.path.join(directory, relative))
        if not relative or not _inside(directory, path) or not os.path.isfile(path):
            raise FileNotFoundError("Project asset file is missing: %s" % relative)
        return path

    def asset(self, project: Any, asset_id: Any) -> tuple[dict[str, Any], str]:
        catalog = self.load(project)
        wanted = str(asset_id or "")
        entry = next((item for item in catalog["assets"]
                      if str(item.get("id") or "") == wanted), None)
        if entry is None:
            raise FileNotFoundError("Project asset %s was not found." % wanted)
        return dict(entry), self._asset_path(project, entry)

    def upload_path(self, project: Any, filename: Any) -> str:
        directory, _name = self._project_dir(project)
        os.makedirs(os.path.join(directory, ".uploads"), exist_ok=True)
        basename = _safe_name(os.path.basename(str(filename or "asset")), "asset")
        suffix = os.path.splitext(basename)[1].lower()
        if suffix not in ALL_MEDIA_EXTENSIONS:
            raise ValueError("Unsupported upload extension %r." % suffix)
        return os.path.join(directory, ".uploads", "%s_%s" % (
            uuid.uuid4().hex, basename))

    @_project_mutation
    def sync_reference_slots(
            self, project: Any, templates: Any) -> dict[str, Any]:
        """Mirror reference metadata without copying or serializing media."""
        if not isinstance(templates, list):
            raise ValueError("Reference templates must be a list.")
        catalog = self.load(project, create=True)
        resolved_tags = {
            str(item.get("tag") or "") for item in catalog["assets"]}
        current = {
            str(item.get("tag") or ""): dict(item)
            for item in catalog.get("reference_slots", [])
            if isinstance(item, dict) and str(item.get("tag") or "")
        }
        incoming_tags = set()
        slots = []
        now = _utc_now()
        for raw in templates:
            if not isinstance(raw, dict):
                raise ValueError("Every reference template must be an object.")
            kind = str(raw.get("kind") or "").strip().lower()
            if kind not in ("image", "video", "audio"):
                raise ValueError(
                    "Reference template kind must be image, video, or audio.")
            tag = _safe_tag(raw.get("tag"), "%s_reference" % kind)
            if tag in incoming_tags:
                raise ValueError("Reference template @%s is duplicated." % tag)
            incoming_tags.add(tag)
            if tag in resolved_tags:
                # A project asset with this tag is already the authoritative
                # binding. Synchronization never overwrites it.
                continue
            role = _role_for_kind(kind, raw.get("role"))
            options = raw.get("options")
            options = dict(options) if isinstance(options, dict) else {}
            previous = current.get(tag)
            created_at = str(
                (previous or {}).get("created_at") or now)
            slot = {
                "id": str((previous or {}).get("id") or
                          ("slot_" + uuid.uuid4().hex)),
                "kind": kind,
                "role": role,
                "tag": tag,
                "content_hash": str(raw.get("content_hash") or ""),
                "available": True,
                "source": "tagged_references",
                "created_at": created_at,
                "updated_at": now,
                "options": options,
            }
            slots.append(slot)
        for tag, previous in current.items():
            if tag in incoming_tags or tag in resolved_tags:
                continue
            stale = dict(previous)
            stale["available"] = False
            stale["updated_at"] = now
            slots.append(stale)
        old_contract = [
            _slot_contract(item)
            for item in catalog.get("reference_slots", [])]
        new_contract = [_slot_contract(item) for item in slots]
        if old_contract == new_contract:
            return self.public_catalog(project)
        catalog["reference_slots"] = slots
        return self._save_catalog(catalog)

    @_project_mutation
    def bind_reference_slot(
            self, project: Any, slot_id: Any, source_path: Any, *,
            role: Any = "", tag: Any = "", original_name: Any = "",
            source_kind: str = "path",
            options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Bind explicitly selected media to one unresolved reference slot."""
        catalog = self.load(project)
        wanted = str(slot_id or "")
        slot = next((item for item in catalog.get("reference_slots", [])
                     if str(item.get("id") or "") == wanted), None)
        if slot is None:
            raise FileNotFoundError(
                "Unresolved reference slot %s was not found." % wanted)
        merged_options = dict(slot.get("options") or {})
        if isinstance(options, dict):
            merged_options.update(options)
        result = self.import_file(
            project, source_path,
            role=role or slot.get("role"), tag=tag or slot.get("tag"),
            original_name=original_name, source_kind=source_kind,
            options=merged_options)
        bound_catalog = result["catalog"]
        bound_catalog["reference_slots"] = [
            item for item in bound_catalog.get("reference_slots", [])
            if str(item.get("id") or "") != wanted]
        result["catalog"] = self._save_catalog(bound_catalog)
        result["bound_slot_id"] = wanted
        return result

    @_project_mutation
    def import_file(self, project: Any, source_path: Any, *, role: Any = "",
                    tag: Any = "", original_name: Any = "",
                    source_kind: str = "path",
                    options: dict[str, Any] | None = None,
                    folder_id: Any = None) -> dict[str, Any]:
        source = os.path.realpath(os.path.abspath(os.path.expanduser(
            str(source_path or "").strip())))
        if not source or not os.path.isfile(source):
            raise FileNotFoundError("Asset source does not exist: %s" % source)
        kind = _media_kind(source)
        role = _role_for_kind(kind, role)
        basename = os.path.basename(str(original_name or source))
        stem = os.path.splitext(basename)[0]
        tag = _safe_tag(tag, stem)
        digest = _file_sha256(source)
        size = int(os.path.getsize(source))
        directory, name = self._project_dir(project)
        catalog = self.load(name)
        folder_id = str(folder_id or "")
        if folder_id and folder_id not in {
                str(item.get("id") or "") for item in catalog.get("folders", [])}:
            raise FileNotFoundError("Asset folder %s was not found." % folder_id)
        if len(catalog["assets"]) >= MAX_CATALOG_ASSETS:
            raise ValueError("Project asset catalog is full.")
        if role == "source_track" and any(
                item.get("role") == "source_track"
                and bool(item.get("enabled", True))
                for item in catalog["assets"]):
            raise ValueError(
                "This project already has an enabled Source track. Disable "
                "or reassign it before importing another one.")
        group = {"image": "images", "video": "videos", "audio": "audio"}[kind]
        suffix = os.path.splitext(source)[1].lower()
        filename = "%s_%s%s" % (
            digest[:16], _safe_name(stem, kind, 80), suffix)
        destination = os.path.join(directory, group, filename)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        if not os.path.isfile(destination) or _file_sha256(destination) != digest:
            temporary = "%s.%s.tmp" % (destination, uuid.uuid4().hex)
            try:
                shutil.copy2(source, temporary)
                if _file_sha256(temporary) != digest:
                    raise ValueError("Imported asset failed its SHA-256 check.")
                os.replace(temporary, destination)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        metadata = _probe_media(destination, kind)
        tag = (_capture_family_tag(catalog, tag, stem)
               if source_kind == "frame_capture" else _unique_tag(catalog, tag, stem))
        now = _utc_now()
        entry = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "role": role,
            "tag": tag,
            "enabled": True,
            "relative_path": os.path.relpath(destination, directory).replace(os.sep, "/"),
            "input_path": os.path.relpath(destination, self.input_root).replace(os.sep, "/"),
            "sha256": digest,
            "size": size,
            "mime_type": mimetypes.guess_type(destination)[0] or "application/octet-stream",
            "original_name": basename,
            "source_kind": str(source_kind or "path")[:40],
            "created_at": now,
            "updated_at": now,
            "metadata": metadata,
            "options": dict(options or {}),
        }
        if folder_id:
            entry["folder_id"] = folder_id
        catalog["assets"].append(entry)
        catalog = self._save_catalog(catalog)
        backup, _name = self._backup_dir(name)
        backup_path = os.path.join(backup, group, filename)
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        if not os.path.isfile(backup_path) or _file_sha256(backup_path) != digest:
            temporary = "%s.%s.tmp" % (backup_path, uuid.uuid4().hex)
            try:
                shutil.copy2(destination, temporary)
                os.replace(temporary, backup_path)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        return {"catalog": catalog, "asset": entry}

    @_project_mutation
    def create_folder(self, project: Any, name: Any, *, color: Any = "") -> dict[str, Any]:
        catalog = self.load(project, create=True)
        if len(catalog.get("folders", [])) >= MAX_ASSET_FOLDERS:
            raise ValueError(
                "Project asset catalog supports at most %d folders." %
                MAX_ASSET_FOLDERS)
        folder_name = _safe_folder_name(name)
        if any(str(item.get("name") or "").casefold() == folder_name.casefold()
               for item in catalog.get("folders", [])):
            raise ValueError("An asset folder named %r already exists." % folder_name)
        now = _utc_now()
        folder = {
            "id": "folder_" + uuid.uuid4().hex,
            "name": folder_name,
            "color": str(color or "")[:32],
            "created_at": now,
            "updated_at": now,
        }
        catalog.setdefault("folders", []).append(folder)
        return {"catalog": self._save_catalog(catalog), "folder": dict(folder)}

    @_project_mutation
    def update_folder(self, project: Any, folder_id: Any,
                      changes: Any) -> dict[str, Any]:
        if not isinstance(changes, dict):
            raise ValueError("Asset folder changes must be a JSON object.")
        catalog = self.load(project)
        wanted = str(folder_id or "")
        folder = next((item for item in catalog.get("folders", [])
                       if str(item.get("id") or "") == wanted), None)
        if folder is None:
            raise FileNotFoundError("Asset folder %s was not found." % wanted)
        if "name" in changes:
            name = _safe_folder_name(changes["name"])
            if any(str(item.get("name") or "").casefold() == name.casefold()
                   and str(item.get("id") or "") != wanted
                   for item in catalog.get("folders", [])):
                raise ValueError("An asset folder named %r already exists." % name)
            folder["name"] = name
        if "color" in changes:
            folder["color"] = str(changes["color"] or "")[:32]
        folder["updated_at"] = _utc_now()
        return {"catalog": self._save_catalog(catalog), "folder": dict(folder)}

    @_project_mutation
    def delete_folder(self, project: Any, folder_id: Any) -> dict[str, Any]:
        catalog = self.load(project)
        wanted = str(folder_id or "")
        folder = next((item for item in catalog.get("folders", [])
                       if str(item.get("id") or "") == wanted), None)
        if folder is None:
            raise FileNotFoundError("Asset folder %s was not found." % wanted)
        catalog["folders"] = [
            item for item in catalog.get("folders", [])
            if str(item.get("id") or "") != wanted]
        moved = 0
        for asset in catalog.get("assets", []):
            if str(asset.get("folder_id") or "") == wanted:
                asset["folder_id"] = ""
                asset["updated_at"] = _utc_now()
                moved += 1
        return {
            "catalog": self._save_catalog(catalog),
            "folder": dict(folder),
            "assets_unfiled": moved,
        }

    @_project_mutation
    def reorder_folders(self, project: Any, folder_ids: Any) -> dict[str, Any]:
        if not isinstance(folder_ids, list):
            raise ValueError("Folder order must be a JSON list of folder IDs.")
        catalog = self.load(project)
        current = [str(item.get("id") or "")
                   for item in catalog.get("folders", [])]
        requested = [str(item or "") for item in folder_ids]
        if (len(requested) != len(set(requested))
                or set(requested) != set(current)):
            raise ValueError(
                "Folder order must contain every current folder ID exactly once.")
        by_id = {str(item.get("id") or ""): item
                 for item in catalog.get("folders", [])}
        catalog["folders"] = [by_id[item] for item in requested]
        return {"catalog": self._save_catalog(catalog)}

    @_project_mutation
    def duplicate(self, project: Any, asset_id: Any, *, tag: Any = "",
                  folder_id: Any = None) -> dict[str, Any]:
        """Create a second catalog card without copying its media bytes."""
        catalog = self.load(project)
        wanted = str(asset_id or "")
        source = next((item for item in catalog["assets"]
                       if str(item.get("id") or "") == wanted), None)
        if source is None:
            raise FileNotFoundError("Project asset %s was not found." % wanted)
        target_folder = (str(source.get("folder_id") or "")
                         if folder_id is None else str(folder_id or ""))
        known_folders = {str(item.get("id") or "")
                         for item in catalog.get("folders", [])}
        if target_folder and target_folder not in known_folders:
            raise FileNotFoundError("Asset folder %s was not found." % target_folder)
        now = _utc_now()
        clone = dict(source)
        clone.update({
            "id": uuid.uuid4().hex,
            "tag": _unique_tag(
                catalog, tag or "%s_copy" % source.get("tag", "asset"),
                "%s_copy" % source.get("tag", "asset")),
            "folder_id": target_folder,
            "parent_asset_id": wanted,
            "source_kind": "catalog_duplicate",
            "created_at": now,
            "updated_at": now,
        })
        if source.get("role") == "source_track":
            clone["enabled"] = False
        clone["options"] = dict(source.get("options") or {})
        if clone["options"].get("audio_tracks") is not None:
            clone["options"]["audio_tracks"] = {
                role: clone["id"] if value == wanted else value
                for role, value in clone["options"]["audio_tracks"].items()}
        clone["metadata"] = dict(source.get("metadata") or {})
        catalog["assets"].append(clone)
        return {"catalog": self._save_catalog(catalog), "asset": dict(clone)}

    def operation_asset(self, project: Any, operation_id: Any) -> dict[str, Any] | None:
        wanted = str(operation_id or "")[:128]
        if not wanted:
            return None
        catalog = self.load(project)
        existing = next((item for item in catalog["assets"]
                         if str((item.get("transform") or {}).get(
                             "operation_id") or "") == wanted), None)
        if existing is None:
            return None
        return {"catalog": catalog, "asset": dict(existing), "reused": True}

    def prepare_image_crop(self, project: Any, asset_id: Any,
                           crop: Any, target: Any) -> tuple[dict[str, Any], Any, dict[str, int]]:
        if Image is None or ImageOps is None:
            raise RuntimeError("Pillow is required for project image variants.")
        entry, path = self.asset(project, asset_id)
        if entry.get("kind") != "image":
            raise ValueError("Only image assets can create cropped variants.")
        with Image.open(path) as opened:
            oriented = ImageOps.exif_transpose(opened).copy()
        geometry = _image_operation_geometry(oriented, crop, target)
        x, y, width, height, target_width, target_height = geometry
        cropped = oriented.crop((x, y, x + width, y + height))
        values = {
            "x": x, "y": y, "width": width, "height": height,
            "target_width": target_width, "target_height": target_height,
            "source_width": int(oriented.width),
            "source_height": int(oriented.height),
        }
        return entry, cropped, values

    @_project_mutation
    def register_derived_image(
            self, project: Any, parent_asset_id: Any, rendered_path: Any, *,
            tag: Any = "", folder_id: Any = None,
            transform: dict[str, Any] | None = None,
            operation_id: Any = "") -> dict[str, Any]:
        parent, _path = self.asset(project, parent_asset_id)
        if parent.get("kind") != "image":
            raise ValueError("Only image assets can own image variants.")
        catalog = self.load(project)
        operation_id = str(operation_id or "")[:128]
        if operation_id:
            existing = next((item for item in catalog["assets"]
                             if str((item.get("transform") or {}).get(
                                 "operation_id") or "") == operation_id), None)
            if existing is not None:
                return {"catalog": catalog, "asset": dict(existing), "reused": True}
        target_folder = (str(parent.get("folder_id") or "")
                         if folder_id is None else str(folder_id or ""))
        result = self.import_file(
            project, rendered_path,
            role=parent.get("role"),
            tag=tag or "%s_variant" % parent.get("tag", "asset"),
            original_name="%s_variant.png" % parent.get("tag", "asset"),
            source_kind="derived_image",
            options=dict(parent.get("options") or {}))
        derived_id = str(result["asset"]["id"])
        catalog = result["catalog"]
        derived = next(item for item in catalog["assets"]
                       if str(item.get("id") or "") == derived_id)
        derived["folder_id"] = target_folder
        derived["parent_asset_id"] = str(parent_asset_id or "")
        lineage = dict(transform or {})
        if operation_id:
            lineage["operation_id"] = operation_id
        derived["transform"] = lineage
        derived["updated_at"] = _utc_now()
        saved = self._save_catalog(catalog)
        return {"catalog": saved, "asset": dict(derived), "reused": False}

    def derive_image(
            self, project: Any, asset_id: Any, *, crop: Any, target: Any,
            resample: Any = "lanczos", tag: Any = "", folder_id: Any = None,
            operation_id: Any = "") -> dict[str, Any]:
        existing = self.operation_asset(project, operation_id)
        if existing is not None:
            return existing
        _entry, cropped, geometry = self.prepare_image_crop(
            project, asset_id, crop, target)
        resample_name, resample_mode = _image_resampling(resample)
        if "A" in cropped.getbands() or "transparency" in cropped.info:
            cropped = cropped.convert("RGBA")
        output_size = (geometry["target_width"], geometry["target_height"])
        if cropped.size != output_size:
            cropped = cropped.resize(output_size, resample=resample_mode)
        if cropped.mode not in ("RGB", "RGBA", "L", "LA"):
            cropped = cropped.convert("RGBA" if "A" in cropped.getbands() else "RGB")
        temporary = self.upload_path(project, "%s_variant.png" % asset_id)
        try:
            cropped.save(temporary, format="PNG")
            return self.register_derived_image(
                project, asset_id, temporary, tag=tag,
                folder_id=folder_id,
                operation_id=operation_id,
                transform={
                    "kind": "crop_resize",
                    "crop": {key: geometry[key]
                             for key in ("x", "y", "width", "height")},
                    "target": {
                        "width": geometry["target_width"],
                        "height": geometry["target_height"],
                    },
                    "source": {
                        "width": geometry["source_width"],
                        "height": geometry["source_height"],
                    },
                    "resample": resample_name,
                })
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @_project_mutation
    def update(self, project: Any, asset_id: Any, changes: Any) -> dict[str, Any]:
        if not isinstance(changes, dict):
            raise ValueError("Asset changes must be a JSON object.")
        catalog = self.load(project)
        wanted = str(asset_id or "")
        entry = next((item for item in catalog["assets"]
                      if str(item.get("id") or "") == wanted), None)
        if entry is None:
            raise FileNotFoundError("Project asset %s was not found." % wanted)
        kind = str(entry["kind"])
        if "role" in changes:
            entry["role"] = _role_for_kind(kind, changes["role"])
        if "tag" in changes:
            tag = _safe_tag(changes["tag"], entry.get("tag") or "asset")
            if any(str(item.get("tag") or "") == tag
                   and str(item.get("id") or "") != wanted
                   and bool(item.get("enabled", True))
                   for item in catalog["assets"]):
                raise ValueError("Another enabled project asset already uses @%s." % tag)
            entry["tag"] = tag
        if "enabled" in changes:
            entry["enabled"] = bool(changes["enabled"])
        if "folder_id" in changes:
            folder_id = str(changes["folder_id"] or "")
            known_folders = {str(item.get("id") or "")
                             for item in catalog.get("folders", [])}
            if folder_id and folder_id not in known_folders:
                raise FileNotFoundError(
                    "Asset folder %s was not found." % folder_id)
            entry["folder_id"] = folder_id
        if "lyrics" in changes:
            if kind != "audio":
                raise ValueError("Lyrics can only be attached to audio assets.")
            lyrics = str(changes["lyrics"] or "").replace(
                "\r\n", "\n").replace("\r", "\n")
            if len(lyrics) > MAX_ASSET_LYRICS_CHARACTERS:
                raise ValueError(
                    "Asset lyrics cannot exceed %d characters." %
                    MAX_ASSET_LYRICS_CHARACTERS)
            if lyrics:
                entry["lyrics"] = lyrics
            else:
                entry.pop("lyrics", None)
        if "options" in changes:
            if not isinstance(changes["options"], dict):
                raise ValueError("Asset options must be a JSON object.")
            options = dict(entry.get("options") or {})
            options.update(changes["options"])
            entry["options"] = options
        if bool(entry.get("enabled", True)):
            if any(str(item.get("tag") or "") == str(entry.get("tag") or "")
                   and str(item.get("id") or "") != wanted
                   and bool(item.get("enabled", True))
                   for item in catalog["assets"]):
                raise ValueError(
                    "Another enabled project asset already uses @%s." %
                    entry.get("tag"))
            if (entry.get("role") == "source_track" and any(
                    item.get("role") == "source_track"
                    and str(item.get("id") or "") != wanted
                    and bool(item.get("enabled", True))
                    for item in catalog["assets"])):
                raise ValueError(
                    "This project already has an enabled Source track.")
        entry["updated_at"] = _utc_now()
        catalog = self._save_catalog(catalog)
        return {"catalog": catalog, "asset": dict(entry)}

    @_project_mutation
    def reorder(self, project: Any, asset_ids: Any) -> dict[str, Any]:
        """Persist one exact permutation of the project's asset cards."""
        if not isinstance(asset_ids, list):
            raise ValueError("Asset order must be a JSON list of asset IDs.")
        catalog = self.load(project)
        current_ids = [str(item.get("id") or "")
                       for item in catalog["assets"]]
        requested = [str(item or "") for item in asset_ids]
        if (len(requested) != len(set(requested))
                or set(requested) != set(current_ids)):
            raise ValueError(
                "Asset order must contain every current asset ID exactly once.")
        by_id = {
            str(item.get("id") or ""): item for item in catalog["assets"]}
        catalog["assets"] = [by_id[asset_id] for asset_id in requested]
        return {"catalog": self._save_catalog(catalog)}

    @_project_mutation
    def delete(self, project: Any, asset_id: Any) -> dict[str, Any]:
        """Remove a catalog asset and its project-owned media copies."""
        catalog = self.load(project)
        wanted = str(asset_id or "")
        entry = next((item for item in catalog["assets"]
                      if str(item.get("id") or "") == wanted), None)
        if entry is None:
            raise FileNotFoundError("Project asset %s was not found." % wanted)
        removed = dict(entry)
        for owner in catalog["assets"]:
            bindings = (owner.get("options") or {}).get("audio_tracks") or {}
            if str(owner.get("id") or "") != wanted and wanted in bindings.values():
                raise ValueError(
                    "This audio is part of %s's track group. Detach it there "
                    "before deleting it." % (owner.get("tag") or owner["id"]))
        catalog["assets"] = [
            item for item in catalog["assets"]
            if str(item.get("id") or "") != wanted]
        remaining_paths = {
            str(item.get("relative_path") or "")
            for item in catalog["assets"]}
        saved = self._save_catalog(catalog)

        deleted_files = 0
        relative = str(removed.get("relative_path") or "")
        if relative and relative not in remaining_paths:
            project_dir, name = self._project_dir(project)
            backup_dir, _name = self._backup_dir(name)
            for root in (project_dir, backup_dir):
                path = os.path.realpath(os.path.join(root, relative))
                if not _inside(root, path):
                    raise ValueError("Project asset media path escapes its store.")
                try:
                    os.unlink(path)
                    deleted_files += 1
                except FileNotFoundError:
                    pass

        project_dir, _name = self._project_dir(project)
        preview_dir = os.path.join(project_dir, "previews")
        preview_prefix = wanted + "_"
        if os.path.isdir(preview_dir):
            with os.scandir(preview_dir) as previews:
                for preview in previews:
                    if (preview.is_file(follow_symlinks=False)
                            and preview.name.startswith(preview_prefix)):
                        os.unlink(preview.path)
                        deleted_files += 1
        return {
            "catalog": saved,
            "asset": removed,
            "deleted_files": deleted_files,
        }

    def input_media(self, query: Any = "") -> list[dict[str, Any]]:
        needle = str(query or "").strip().lower()
        result = []
        if not os.path.isdir(self.input_root):
            return result
        for root, directories, files in os.walk(self.input_root):
            # Walk normal input subfolders recursively, but omit storage that
            # would either duplicate this catalog or flood the picker with
            # transient clipboard/Clipspace images.
            directories[:] = [
                name for name in directories
                if name.casefold() not in INPUT_BROWSER_EXCLUDED_DIRECTORIES
            ]
            for filename in files:
                suffix = os.path.splitext(filename)[1].lower()
                if suffix not in ALL_MEDIA_EXTENSIONS:
                    continue
                path = os.path.join(root, filename)
                relative = os.path.relpath(path, self.input_root).replace(os.sep, "/")
                if needle and needle not in relative.lower():
                    continue
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                result.append({
                    "path": relative,
                    "kind": _media_kind(path),
                    "size": int(stat.st_size),
                    "modified": float(stat.st_mtime),
                })
                if len(result) >= MAX_INPUT_RESULTS:
                    break
            if len(result) >= MAX_INPUT_RESULTS:
                break
        return sorted(result, key=lambda item: item["path"].lower())

    def input_path(self, relative: Any) -> str:
        value = str(relative or "").strip()
        path = os.path.realpath(os.path.join(self.input_root, value))
        if not value or not _inside(self.input_root, path) or not os.path.isfile(path):
            raise FileNotFoundError("ComfyUI input asset was not found: %s" % value)
        return path

    def project_catalogs(self, query: Any = "") -> list[dict[str, Any]]:
        """List existing live Carousel projects, including empty catalogs."""
        needle = str(query or "").strip().lower()
        result = []
        if not os.path.isdir(self.projects_root):
            return result
        with os.scandir(self.projects_root) as projects:
            for project in projects:
                if (not project.is_dir(follow_symlinks=False)
                        or needle and needle not in project.name.lower()
                        or not os.path.isfile(os.path.join(
                            project.path, "catalog.json"))):
                    continue
                try:
                    catalog = self.load(project.name)
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                result.append({
                    "project": project.name,
                    "asset_count": len(catalog.get("assets", [])),
                    "unassigned_count": len(
                        catalog.get("reference_slots", [])),
                    "folder_count": len(catalog.get("folders", [])),
                    "updated_at": str(catalog.get("updated_at") or ""),
                    "revision": str(catalog.get("revision") or ""),
                })
        result.sort(key=lambda item: item["project"].lower())
        result.sort(key=lambda item: item["updated_at"], reverse=True)
        return result[:MAX_INPUT_RESULTS]

    def projects(self, query: Any = "", *, exclude_project: Any = "") -> list[dict[str, Any]]:
        """List live Carousel catalogs without exposing arbitrary input files."""
        needle = str(query or "").strip().lower()
        excluded = (_safe_project(exclude_project)
                    if str(exclude_project or "").strip() else "")
        result = []
        remaining = MAX_INPUT_RESULTS
        if not os.path.isdir(self.projects_root):
            return result
        with os.scandir(self.projects_root) as projects:
            for project in projects:
                if remaining <= 0:
                    break
                if (not project.is_dir(follow_symlinks=False)
                        or project.name == excluded):
                    continue
                try:
                    catalog = self.load(project.name)
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                assets = []
                for raw in catalog.get("assets", []):
                    if not isinstance(raw, dict):
                        continue
                    searchable = " ".join((
                        project.name,
                        str(raw.get("tag") or ""),
                        str(raw.get("original_name") or ""),
                        str(raw.get("role") or ""),
                    )).lower()
                    if needle and needle not in searchable:
                        continue
                    try:
                        self._asset_path(project.name, raw)
                    except (OSError, TypeError, ValueError):
                        continue
                    assets.append(dict(raw))
                    remaining -= 1
                    if remaining <= 0:
                        break
                if assets:
                    result.append({
                        "project": project.name,
                        "revision": str(catalog.get("revision") or ""),
                        "assets": assets,
                    })
        return sorted(result, key=lambda item: item["project"].lower())

    def import_project_asset(
            self, project: Any, source_project: Any, asset_id: Any, *,
            slot_id: Any = "") -> dict[str, Any]:
        """Copy one asset from another live Carousel into this project."""
        target_name = _safe_project(project)
        source_name = _safe_project(source_project)
        if target_name == source_name:
            raise ValueError(
                "Choose another Run. Use Duplicate for an asset already in "
                "this Carousel.")
        source_entry, source_path = self.asset(source_name, asset_id)
        wanted_slot = str(slot_id or "")
        options = dict(source_entry.get("options") or {})
        bindings = audio_track_bindings(
            options.pop("audio_tracks", None), self.load(source_name)["assets"])
        # Resolve every dependency before copying anything. Track bindings are
        # project-local IDs; they cannot be copied verbatim to another Run.
        dependencies = {
            value: self.asset(source_name, value)
            for value in (bindings or {}).values()
            if value and value != str(source_entry["id"])} if not wanted_slot else {}
        if wanted_slot:
            result = self.bind_reference_slot(
                target_name, wanted_slot, source_path,
                original_name=source_entry.get("original_name", ""),
                source_kind="project")
        else:
            result = self.import_file(
                target_name, source_path,
                role=source_entry.get("role", ""),
                tag=source_entry.get("tag", ""),
                original_name=source_entry.get("original_name", ""),
                source_kind="project",
                options=options)
        if bindings and not wanted_slot:
            mapped = {str(source_entry["id"]): result["asset"]["id"]}
            for value, (entry, path) in dependencies.items():
                track_options = dict(entry.get("options") or {})
                track_options.pop("audio_tracks", None)
                imported = self.import_file(
                    target_name, path, role="audio_reference",
                    tag=entry.get("tag", ""),
                    original_name=entry.get("original_name", ""),
                    source_kind="project", options=track_options)
                mapped[value] = imported["asset"]["id"]
                self.update(target_name, mapped[value], {
                    "enabled": False, "lyrics": str(entry.get("lyrics") or "")})
            result = self.update(target_name, result["asset"]["id"], {
                "options": {"audio_tracks": {
                    role: mapped[value] if value else ""
                    for role, value in bindings.items()}}})
        lyrics = str(source_entry.get("lyrics") or "")
        if lyrics and result.get("asset", {}).get("kind") == "audio":
            bound_slot_id = result.get("bound_slot_id")
            result = self.update(
                target_name, result["asset"]["id"], {"lyrics": lyrics})
            if bound_slot_id:
                result["bound_slot_id"] = bound_slot_id
        result["source_project"] = source_name
        result["source_asset_id"] = str(source_entry.get("id") or "")
        return result

    def backups(self) -> list[dict[str, Any]]:
        result = []
        if not os.path.isdir(self.chains_root):
            return result
        with os.scandir(self.chains_root) as runs:
            for run in runs:
                if not run.is_dir(follow_symlinks=False):
                    continue
                path = os.path.join(run.path, "project_assets", "catalog.json")
                if not os.path.isfile(path):
                    continue
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        catalog = json.load(handle)
                    assets = catalog.get("assets") if isinstance(catalog, dict) else None
                    if not isinstance(assets, list):
                        continue
                    result.append({
                        "run_name": run.name,
                        "revision": str(catalog.get("revision") or ""),
                        "assets": [dict(item) for item in assets if isinstance(item, dict)],
                    })
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
        return sorted(result, key=lambda item: item["run_name"].lower())

    def backup_asset_path(self, run_name: Any, asset_id: Any) -> tuple[dict[str, Any], str]:
        backup, name = self._backup_dir(run_name)
        path = os.path.join(backup, "catalog.json")
        if not os.path.isfile(path):
            raise FileNotFoundError("H3 chain %s has no project asset backup." % name)
        with open(path, "r", encoding="utf-8") as handle:
            catalog = json.load(handle)
        entry = next((item for item in catalog.get("assets", [])
                      if str(item.get("id") or "") == str(asset_id or "")), None)
        if entry is None:
            raise FileNotFoundError("Backup asset %s was not found." % asset_id)
        relative = str(entry.get("relative_path") or "")
        source = os.path.realpath(os.path.join(backup, relative))
        if not _inside(backup, source) or not os.path.isfile(source):
            raise FileNotFoundError("Backup media is missing: %s" % relative)
        return dict(entry), source

    def _preview_path(self, project: Any, entry: dict[str, Any], suffix: str) -> str:
        directory, _name = self._project_dir(project)
        return os.path.join(directory, "previews", "%s_%s%s" % (
            entry["id"], str(entry.get("sha256") or "")[:12], suffix))

    def ensure_poster(self, project: Any, asset_id: Any) -> str:
        return self._ensure_still_preview(
            project, asset_id, ".jpg", (960, 540), 86)

    def ensure_thumbnail(self, project: Any, asset_id: Any) -> str:
        """Return a small cached still used by large carousel collections."""
        return self._ensure_still_preview(
            project, asset_id, ".thumb.jpg", (320, 180), 78)

    def _ensure_still_preview(
            self, project: Any, asset_id: Any, suffix: str,
            bounds: tuple[int, int], quality: int) -> str:
        entry, source = self.asset(project, asset_id)
        if entry["kind"] == "audio":
            raise ValueError("Audio assets do not have poster frames.")
        target = self._preview_path(project, entry, suffix)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isfile(target) and os.path.getsize(target) > 0:
            return target
        temporary = "%s.%s.tmp.jpg" % (target, uuid.uuid4().hex)
        try:
            if entry["kind"] == "image" and Image is not None:
                with Image.open(source) as image:
                    frame = image.convert("RGB")
                    frame.thumbnail(bounds)
                    frame.save(
                        temporary, format="JPEG", quality=quality,
                        optimize=True)
            else:
                ffmpeg = shutil.which("ffmpeg")
                if ffmpeg:
                    width, height = bounds
                    command = [
                        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", "0", "-i", source, "-frames:v", "1",
                        "-vf", "scale='min(%d,iw)':'min(%d,ih)':"
                        "force_original_aspect_ratio=decrease" % (
                            width, height),
                        "-q:v", "3", temporary,
                    ]
                    completed = subprocess.run(
                        command, stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE, timeout=60, check=False)
                    if completed.returncode:
                        raise RuntimeError(completed.stderr.decode(
                            "utf-8", errors="replace")[-1200:])
                elif av is not None and Image is not None:
                    with av.open(source, mode="r") as container:
                        video = next(iter(container.streams.video), None)
                        frame = next(container.decode(video), None) if video else None
                        if frame is None:
                            raise ValueError("Video contains no decodable frame.")
                        image = frame.to_image().convert("RGB")
                        image.thumbnail(bounds)
                        image.save(
                            temporary, format="JPEG", quality=quality)
                else:
                    raise RuntimeError("Video poster generation requires ffmpeg or PyAV/Pillow.")
            if not os.path.isfile(temporary) or os.path.getsize(temporary) <= 0:
                raise RuntimeError("Preview generator produced an empty poster.")
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return target

    @staticmethod
    def _browser_video(entry: dict[str, Any], path: str) -> bool:
        suffix = os.path.splitext(path)[1].lower()
        codec = str((entry.get("metadata") or {}).get("codec") or "").lower()
        return ((suffix in (".mp4", ".m4v", ".mov") and codec in (
            "h264", "av1", "hevc")) or
            (suffix == ".webm" and codec in ("vp8", "vp9", "av1")))

    def ensure_browser_media(self, project: Any, asset_id: Any) -> str:
        entry, source = self.asset(project, asset_id)
        if entry["kind"] != "video" or self._browser_video(entry, source):
            return source
        target = self._preview_path(project, entry, ".mp4")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isfile(target) and os.path.getsize(target) > 0:
            return target
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return source
        temporary = "%s.%s.tmp.mp4" % (target, uuid.uuid4().hex)
        try:
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-i", source, "-vf", "scale='min(1280,iw)':-2",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                "-c:a", "aac", "-b:a", "128k", temporary,
            ]
            completed = subprocess.run(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=max(120, int(float((entry.get("metadata") or {}).get(
                    "duration_seconds") or 0) * 8)), check=False)
            if completed.returncode:
                raise RuntimeError(completed.stderr.decode(
                    "utf-8", errors="replace")[-1600:])
            if not os.path.isfile(temporary) or os.path.getsize(temporary) <= 0:
                raise RuntimeError("Preview proxy generator produced an empty video.")
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return target
