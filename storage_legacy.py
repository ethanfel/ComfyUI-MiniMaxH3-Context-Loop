"""Legacy physical paths behind an explicit storage boundary.

These constructors do not create directories, read metadata, switch branches,
adopt artifacts or change persisted addresses. Callers retain their existing
ownership/locking and integrity checks. Organized-layout writers must not use
this class as a fallback when their own metadata is missing.
"""

from dataclasses import dataclass
import os
from pathlib import PurePosixPath
import re

if __package__:
    from .artifact_paths import artifact_address
else:
    from artifact_paths import artifact_address


@dataclass(frozen=True)
class LegacyStoragePaths:
    project_root: str
    working_root: str

    def generation(self, index):
        stem = "clip_%04d" % index
        return {
            "run_dir": self.project_root,
            "segment": os.path.join(self.project_root, "segments", stem + ".mp4"),
            "blend_segment": os.path.join(self.project_root, "blend_segments", stem + ".mp4"),
            "generated_audio": os.path.join(self.project_root, "generated_audio", stem + ".wav"),
            "checkpoint": os.path.join(self.project_root, "checkpoints", stem + ".safetensors"),
            "metadata": os.path.join(self.working_root, "checkpoints", stem + ".json"),
        }

    def archives(self, revision=None):
        root = self.working_root
        if revision is not None:
            if not re.fullmatch(r"[0-9a-f]{32}", str(revision)):
                raise ValueError("Recovery archive revision must be a revision id.")
            root = os.path.join(self.project_root, "recovery_archives", revision)
        return {name: os.path.join(root, name + ".json")
                for name in ("plan", "workflow", "api_prompt")}

    def chapter(self, directory_name=None):
        if directory_name is None:
            return self.working_root
        chapter_root = os.path.realpath(os.path.join(self.working_root, "chapters"))
        candidate = os.path.realpath(os.path.join(chapter_root, directory_name))
        if os.path.commonpath([chapter_root, candidate]) != chapter_root:
            raise ValueError("H3 chapter output path escapes its Run directory.")
        return candidate

    @staticmethod
    def processing_container(delivery_root):
        return os.path.join(delivery_root, "upscaled")

    @staticmethod
    def processing_profile(delivery_root, name):
        # Names are normalized by the public node's existing _safe_name policy.
        return os.path.abspath(os.path.join(
            LegacyStoragePaths.processing_container(delivery_root), name))

    @staticmethod
    def is_processing_profile(relative_parts):
        return ((len(relative_parts) == 2 and relative_parts[0] == "upscaled") or
                (len(relative_parts) == 4 and relative_parts[0] == "chapters"
                 and relative_parts[2] == "upscaled"))

    @staticmethod
    def processing_directories(profile_root):
        return {name: os.path.join(profile_root, name) for name in (
            "segments", "checkpoints", "prompts", "audio", "partial", "final")}

    @staticmethod
    def processing_files(profile_root, index):
        stem = "clip_%04d" % int(index)
        dirs = LegacyStoragePaths.processing_directories(profile_root)
        return {
            "root": profile_root,
            "segment": os.path.join(dirs["segments"], stem + ".mp4"),
            "checkpoint": os.path.join(dirs["checkpoints"], stem + ".safetensors"),
            "metadata": os.path.join(dirs["checkpoints"], stem + ".json"),
            "prompt": os.path.join(dirs["prompts"], stem + ".txt"),
            "audio": os.path.join(dirs["audio"], stem + ".wav"),
            "manifest": os.path.join(profile_root, "upscale_manifest.json"),
            "partial": os.path.join(dirs["partial"], "through_clip_%04d.manifest.json" % int(index)),
            "final": dirs["final"],
        }

    @staticmethod
    def png_root(delivery_root):
        return os.path.join(delivery_root, "frames")

    @staticmethod
    def png_sequence(delivery_root, name):
        return os.path.join(LegacyStoragePaths.png_root(delivery_root), name)

    @staticmethod
    def processing_metadata(address, run):
        """Validate a legacy immutable take address; retain its original scope.

        This is a syntax check, not filesystem authorization. Destructive callers
        must still reject symlinks/junctions and validate metadata/ownership.
        """
        address = artifact_address(address)
        parts = PurePosixPath(address).parts
        scope = parts
        if len(scope) > 4 and scope[2] == "branches" and re.fullmatch(r"[0-9a-f]{32}", scope[3]):
            scope = scope[:2] + scope[4:]
        if not (scope[:2] == ("h3_chains", run) and (
                len(scope) == 6 and scope[2] == "upscaled" or
                len(scope) == 8 and scope[2] == "chapters" and scope[4] == "upscaled")
                and scope[-2] == "checkpoints"
                and re.fullmatch(r"clip_\d{4}\.[0-9a-f]{32}\.json", scope[-1])):
            raise ValueError("Select an immutable processed checkpoint inside this run.")
        return {"metadata": address, "profile_root": str(PurePosixPath(address).parent.parent)}
