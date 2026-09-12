"""Organized layout contract: paths only, not an enabled storage format/writer.

Three top-level areas: media, exports, project. Relationships live in metadata;
branch/chapter/profile labels never add more directory levels to saved media.
Constructing a layout creates NOTHING. Legacy runtime paths remain separate.
"""

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re

if __package__:
    from .artifact_paths import artifact_address, is_link_or_junction
    from .checkpoint_variants import processing_stage
else:
    from artifact_paths import artifact_address, is_link_or_junction
    from checkpoint_variants import processing_stage


LAYOUT = "h3_organized_layout_v2"
MEDIA_STAGES = ("generation", "alternate", "derope", "latent_upscale",
                "pixel_upscale", "video_refine", "custom")
# Only these are disposable. Conditioning objects and recovery are intentionally
# absent: calling something a cache must not weaken its retention contract.
DISPOSABLE_GROUPS = ("previews", "thumbnails", "diagnostics")
PROJECT_GROUPS = ("assets", "reference_cache", "recovery", "history", "reviews",
                  "branches", "passes", "cuts", "takes", "aliases", "legacy",
                  "roots", "commits", "jobs", "tombstones", "payloads")
_ID = re.compile(r"[0-9a-f]{32}\Z")


def storage_stage(*, take_kind=None, profile_config=None):
    """Classify by saved semantics, never folder names or model-name guesses."""
    if profile_config is None:
        if take_kind in (None, "original", "generation"):
            return "generation"
        if take_kind == "editorial_alternate":
            return "alternate"
        raise ValueError("Unknown generation take kind; a storage stage is required.")
    if not isinstance(profile_config, dict):
        raise ValueError("Processing storage needs its saved profile configuration.")
    stage = processing_stage(profile_config)
    if stage in ("derope", "latent_upscale", "pixel_upscale"):
        return stage
    return "video_refine" if profile_config.get("backend") == "ltx_2_5" else "custom"


def workflow_storage_route(kind, *, take_kind=None, profile_config=None):
    """Describe the persistence boundary, including whole-video workflows.

    A VIDEO adapter feeding a third-party saver does not create H3 processing
    checkpoints. Its destination needs an explicit export integration; claiming
    per-scene ownership/resume for that workflow would be incorrect.
    """
    if kind == "external_video":
        return {"media_stage": None, "export_kind": "video", "scene_checkpoints": False,
                "managed_writer": False, "integration": "explicit_export_boundary_required"}
    if kind not in ("generation", "processing"):
        raise ValueError("Unknown workflow storage boundary.")
    if kind == "processing" and profile_config is None:
        raise ValueError("Processing workflow requires a saved profile configuration.")
    if kind == "generation" and profile_config is not None:
        raise ValueError("Generation workflow cannot have a processing profile.")
    return {"media_stage": storage_stage(take_kind=take_kind, profile_config=profile_config),
            "export_kind": "video_or_png", "scene_checkpoints": True,
            "managed_writer": True, "integration": "h3_take_and_export_service"}


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("Storage IDs must be full 32-character lowercase hexadecimal IDs.")
    return value


@dataclass(frozen=True)
class OrganizedStorageLayout:
    """Project-relative addresses for a future explicitly activated V2 root.

    Storage IDs are allocated independently of old revision IDs. This permits
    colliding legacy scopes and shared media to retain their exact identities.
    There is deliberately no auto-detection, migration, mkdir or write API here.
    """

    project_root: str
    path_budget: int = 240

    def __post_init__(self):
        if type(self.path_budget) is not int or self.path_budget < 64:
            raise ValueError("Storage path budget must be an integer of at least 64 characters.")

    def _address(self, *parts):
        address = artifact_address("/".join(parts))
        self.check_budget(address)
        return address

    def check_budget(self, address, *, staging_suffix=""):
        address = artifact_address(address)
        if not isinstance(staging_suffix, str) or any(c in staging_suffix for c in "/\\\0:"):
            raise ValueError("Staging suffix cannot contain path separators or streams.")
        # Count the host's full prefix too, without silently shortening IDs or
        # labels. UTF-16 units are the conservative Windows path-length metric.
        root = str(self.project_root).rstrip("/\\")
        full = root + "/" + address + staging_suffix
        length = max(len(full), len(full.encode("utf-16-le")) // 2)
        if length > self.path_budget:
            raise ValueError("Storage path requires %d characters; configured budget is %d. "
                             "Use a shorter output/project root, not truncated identities."
                             % (length, self.path_budget))
        return length

    def check_atomic_json_budget(self, address):
        """Budget the actual bounded same-directory JSON temporary filename."""
        address = artifact_address(address)
        temporary = str(PurePosixPath(address).parent / (".tmp-" + "f" * 32))
        return max(self.check_budget(address), self.check_budget(temporary))

    def media(self, stage, storage_id, *, pass_id=None):
        if stage not in MEDIA_STAGES:
            raise ValueError("Unknown saved-media storage stage.")
        if stage in ("generation", "alternate"):
            if pass_id is not None:
                raise ValueError("Generation/ALT storage cannot have a processing pass.")
            root = ("media", stage, _id(storage_id))
        else:
            # Group a pass's outputs together, without nesting source branches
            # or chapters. Cross-pass reuse points to the owning pass's take.
            root = ("media", stage, _id(pass_id), _id(storage_id))
        return {role: self._address(*root, filename) for role, filename in (
            ("video", "video.mp4"), ("checkpoint", "checkpoint.safetensors"),
            ("audio", "audio.wav"), ("overlap", "overlap.mp4"))}

    def take_metadata(self, storage_id):
        return self._address("project", "takes", _id(storage_id) + ".json")

    def take_prompt(self, storage_id):
        return self._address("project", "takes", _id(storage_id) + ".prompt.txt")

    def export(self, kind, export_id):
        if kind not in ("png", "video", "audio"):
            raise ValueError("Export kind must be png, video or audio.")
        return self._address("exports", kind, _id(export_id))

    def export_file(self, kind, export_id, role, *, frame_number=None, revision=None):
        root = self.export(kind, export_id)
        if revision is not None:
            if role != "audio":
                raise ValueError("Only a soundtrack can use a versioned export filename.")
            filename = _id(revision) + ".wav"
        elif role == "frame" and kind == "png":
            if type(frame_number) is not int or frame_number < 0:
                raise ValueError("PNG frame numbers must be non-negative integers.")
            filename = "frame_%08d.png" % frame_number
        else:
            names = {"index": "export.json", "audio": "audio.wav", "subtitles": "subtitles.srt"}
            if kind == "video":
                names["video"] = "video.mp4"
            if role not in names:
                raise ValueError("Unsupported export artifact role.")
            filename = names[role]
        return self._address(root, filename)

    def project_data(self, group, *relative_parts):
        if group not in PROJECT_GROUPS:
            raise ValueError("Unknown project data category.")
        return self._address("project", group, *relative_parts)

    def optional(self, group, *relative_parts):
        if group not in DISPOSABLE_GROUPS:
            raise ValueError("Recovery, assets and conditioning cache are not disposable optional data.")
        return self._address("project", "optional", group, *relative_parts)

    def native_path(self, address):
        """Confined local lookup for tools/tests, without filesystem writes."""
        address = artifact_address(address)
        self.check_budget(address)
        root_text = str(self.project_root)
        if os.name != "nt" and PureWindowsPath(root_text).drive:
            raise ValueError("Cannot resolve a Windows storage root on this host.")
        root = Path(os.path.abspath(root_text))
        if is_link_or_junction(root):
            raise ValueError("Storage roots cannot be links or junctions.")
        root = root.resolve()
        path = root
        for part in PurePosixPath(address).parts:
            path = path / part
            if is_link_or_junction(path):
                raise ValueError("Storage lookup cannot follow links or junctions.")
        if not path.resolve().is_relative_to(root):
            raise ValueError("Storage lookup leaves its project.")
        return path
