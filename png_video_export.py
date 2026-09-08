"""Bounded-memory, scene-transactional PNG export from native file-backed VIDEO.

No VAE, full-scene tensor, or concatenated video is created here. The input
VIDEO is returned unchanged only after the current scene is durable on disk.
"""

import concurrent.futures
from contextlib import contextmanager
from copy import deepcopy
from fractions import Fraction
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import zlib

from . import png_export_transaction as transaction
from . import processing_persistence as persistence
from . import png_export_variants as variants


FORMAT = "h3_video_png_sequence_v1"


def bit_depth(value):
    if str(value) not in ("8", "16"):
        raise ValueError("png_bit_depth must be 8 or 16.")
    return int(value)


def write_png16(path, pixels, compression, metadata):
    """Pillow does not support RGB16; write standard PNG RGB16 rows directly."""
    import numpy as np

    if pixels.dtype != np.uint16 or pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError("16-bit PNG requires one uint16 RGB frame.")

    def chunk(handle, kind, data):
        handle.write(struct.pack(">I", len(data)))
        handle.write(kind)
        handle.write(data)
        handle.write(struct.pack(">I", zlib.crc32(data, zlib.crc32(kind)) & 0xffffffff))

    # The caller uses a private scene staging folder, or its own atomic path.
    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        chunk(handle, b"IHDR", struct.pack(">IIBBBBB", pixels.shape[1], pixels.shape[0], 16, 2, 0, 0, 0))
        for key, value in metadata.items():
            if value is not None:
                chunk(handle, b"iTXt", str(key).encode("latin-1") + b"\0\0\0\0\0" + str(value).encode("utf-8"))
        compressor = zlib.compressobj(int(compression))
        for row in pixels:
            data = compressor.compress(b"\0" + row.astype(">u2", copy=False).tobytes())
            if data:
                chunk(handle, b"IDAT", data)
        chunk(handle, b"IDAT", compressor.flush())
        chunk(handle, b"IEND", b"")


def _source_path(video):
    from comfy_api.latest import InputImpl

    # Base get_stream_source/get_components can materialize the whole video.
    if not isinstance(video, InputImpl.VideoFromFile):
        raise ValueError("PNG VIDEO export requires a file-backed VIDEO; do not convert the sequence to IMAGE.")
    start, duration = video.get_active_trim_window()
    if start or duration or getattr(video, "_VideoFromFile__crop", None) is not None:
        raise ValueError("Save/reload native VIDEO trims or crops before PNG export. RAW scene trim is applied from state.")
    source = video.get_stream_source()
    if not isinstance(source, str) or not Path(source).is_file():
        raise ValueError("PNG VIDEO export requires an existing video file, not an in-memory buffer.")
    return Path(source)


def _safe_path(root, value):
    path = Path(value)
    if ".." in path.parts:
        raise ValueError("PNG output folder cannot contain '..'.")
    if not path.is_absolute():
        path = root / path
    if path == root or not path.is_relative_to(root):
        raise ValueError("Choose a PNG subfolder inside the ComfyUI output directory.")
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise ValueError("PNG output paths must not follow symbolic links.")
    return path


@contextmanager
def _folder_lock(root, directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = _safe_path(root, directory / ".png_export.lock")
    with path.open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0, os.SEEK_END)
                if not handle.tell():
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("Another PNG export is writing this folder; retry later or choose another folder.") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _file_identity(path):
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _publish_frame(source, target):
    try:
        os.link(source, target)
        return
    except OSError as exc:
        if exc.errno not in (errno.EACCES, errno.EPERM, errno.EXDEV, errno.EOPNOTSUPP, errno.ENOSYS):
            raise
    # Network shares may deny hard links (EACCES/EPERM) while allowing writes.
    # Exclusive create still enforces real write permissions and never replaces
    # an existing file or symlink; copy with a small bounded buffer.
    with target.open("xb") as handle:
        try:
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, handle, length=1024 * 1024)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            target.unlink()
            raise


def _scene_pixels(chain, path, raw, delivered, bits):
    """Stream and validate one RAW video, yielding only delivered RGB frames."""
    import av
    import numpy as np

    count, origin, size = 0, None, None
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("VIDEO has no picture stream.")
        stream = container.streams.video[0]
        if (stream.average_rate or stream.guessed_rate) != chain.FPS:
            raise ValueError("PNG VIDEO frame rate must match the H3 scene clock (%d fps)." % chain.FPS)
        for frame in container.decode(stream):
            chain._png_export_check_interrupted()
            if count >= raw or frame.pts is None or frame.rotation:
                raise ValueError("VIDEO must contain the exact RAW scene frames, timestamps and unrotated pixels.")
            timestamp = frame.pts * frame.time_base
            if origin is None:
                origin, size = timestamp, (frame.width, frame.height)
            if ((frame.width, frame.height) != size
                    or abs(timestamp - origin - Fraction(count, chain.FPS)) > Fraction(1, 1000)):
                raise ValueError("VIDEO dimensions/timestamps do not match a constant-rate H3 scene.")
            if count >= raw - delivered:
                pixels = frame.to_ndarray(format="rgb48le")
                if bits == 8:
                    pixels = ((pixels.astype(np.uint32) + 128) // 257).astype(np.uint8)
                yield pixels
                del pixels
            count += 1
    if count != raw:
        raise ValueError("VIDEO frame count does not match the RAW scene; scene was not committed.")


def _pixel_hasher(bits):
    return hashlib.sha256(("h3-png-rgb%d-v1" % bits).encode("ascii"))


def _hash_pixels(hasher, pixels):
    hasher.update(struct.pack(">II", pixels.shape[1], pixels.shape[0]))
    hasher.update(pixels.tobytes())


def _matching_pixels(chain, path, raw, delivered, bits, existing, directory):
    hasher = _pixel_hasher(bits)
    for pixels in _scene_pixels(chain, path, raw, delivered, bits):
        _hash_pixels(hasher, pixels)
    expected = existing.get("pixel_sha256")
    if not expected:
        # Compatibility with pre-journal exports: verify decoded PNG content,
        # not PNG compression/metadata or the video container's random IDs.
        import av
        saved = _pixel_hasher(bits)
        for item in existing["files"]:
            chain._png_export_check_interrupted()
            with av.open(str(directory / item["file"])) as container:
                pixels = next(container.decode(video=0)).to_ndarray(format="rgb48le" if bits == 16 else "rgb24")
                _hash_pixels(saved, pixels)
        expected = saved.hexdigest()
    return hasher.hexdigest() == expected


def _seed_variant_prefix(chain, root, directory, previous, marker, config, contracts):
    """Copy already verified earlier scenes when a rerender forks mid-sequence.

    The reservation keeps the exact prefix snapshot so retries never adopt a
    newer source index. Copies are bounded, independent files (not hard links
    to editable older exports), committed one scene at a time by the journal.
    """
    prefix = (marker or {}).get("prefix")
    if prefix is None:
        return previous
    if not isinstance(prefix, dict) or not isinstance(prefix.get("record"), dict):
        raise ValueError("Invalid PNG variant prefix; saved exports were kept.")
    source_dir = _safe_path(root, prefix.get("directory", ""))
    record = prefix["record"]
    clips = record.get("clips")
    if (source_dir == directory or record.get("format") != FORMAT
            or record.get("settings") != config or not isinstance(clips, list) or not clips
            or not isinstance(clips[0], dict)):
        raise ValueError("Invalid PNG variant prefix settings; saved exports were kept.")
    expected_frame = config["first_frame_number"]
    expected_scene = clips[0].get("index")
    for clip in clips:
        if (not isinstance(clip, dict) or clip.get("index") != expected_scene
                or not isinstance(expected_scene, int)
                or clip.get("source_contract") != contracts.get(expected_scene)
                or not isinstance(clip.get("delivered_frames"), int)
                or clip["delivered_frames"] <= 0
                or clip.get("first_frame_number") != expected_frame
                or clip.get("last_frame_number") != expected_frame + clip["delivered_frames"] - 1
                or not isinstance(clip.get("files"), list)
                or len(clip["files"]) != clip["delivered_frames"]
                or expected_scene >= marker.get("first_changed_scene", 0)):
            raise ValueError("Invalid PNG variant prefix branch/order; saved exports were kept.")
        for offset, item in enumerate(clip["files"]):
            if not isinstance(item, dict) or item.get("file") != "frame_%08d.png" % (expected_frame + offset):
                raise ValueError("Invalid PNG variant prefix frame address.")
        expected_frame += clip["delivered_frames"]
        expected_scene += 1
    committed = (previous or {}).get("clips", [])
    # File mtimes change on a bounded copy; normalize only these stat hints.
    if not transaction._same_record({"clips": committed[:len(clips)]},
                                    {"clips": clips[:len(committed)]}):
        raise ValueError("PNG variant prefix changed; saved exports were kept.")
    for clip in clips[len(committed):]:
        copied = deepcopy(clip)
        with transaction.staging(chain, directory, clip["index"]) as stage:
            for item in copied["files"]:
                chain._png_export_check_interrupted()
                incoming = _safe_path(root, source_dir / item["file"])
                target = _safe_path(root, stage / item["file"])
                with incoming.open("rb") as reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
                persistence.sync_file(target)
                copied_item = chain._png_export_file_record(str(target))
                if copied_item["size"] != item["size"] or copied_item["sha256"] != item["sha256"]:
                    raise ValueError("PNG variant prefix was edited during copying; saved exports were kept.")
                item.update(copied_item)
            next_record = {**record, "clips": (previous or {}).get("clips", []) + [copied],
                           "frame_count": copied["last_frame_number"] + 1 - config["first_frame_number"],
                           "last_scene": copied["index"], "complete": False}
            transaction.publish(chain, root, directory, stage, previous, next_record, _safe_path, _publish_frame)
            previous = next_record
    return previous


def export_video(chain, video, state, export_name, output_folder, first_frame_number,
                 png_compression, png_bit_depth, embed_workflow, save_workers,
                 checkpoint_verification, reuse_existing):
    from . import upscale_nodes as upscale

    if not isinstance(state, dict) or state.get("profile_config", {}).get("backend") != "pixel":
        raise ValueError("Connect the pixel Upscale Current Scene state alongside VIDEO to export each scene inside the loop.")
    bits = bit_depth(png_bit_depth)
    verification = str(checkpoint_verification)
    if verification not in ("cached", "strict"):
        raise ValueError("checkpoint_verification must be cached or strict.")
    index = int(state["index"])
    source = upscale._source_segment(state)
    raw, delivered = int(source["raw_frames"]), int(source["delivered_frames"])
    if not 0 < delivered <= raw or int(first_frame_number) < 0:
        raise ValueError("Invalid scene frame counts or first frame number for PNG export.")
    path = _source_path(video)
    root = Path(chain._output_root()).resolve()
    default = Path(upscale._state_profile_paths(state, index)["root"]) / "frames" / chain._safe_name(export_name, "png_sequence")
    directory = _safe_path(root, str(output_folder).strip() or default)
    config = {"run_name": state["run_name"], "profile": state["profile"],
              "profile_config": state["profile_config"], "first_frame_number": int(first_frame_number),
              "png_bit_depth": bits, "png_compression": max(0, min(9, int(png_compression))),
              "embed_workflow": bool(embed_workflow)}
    contracts = {int(item["index"]): upscale._upscale_source_contract(item)
                 for item in state["source_manifest"]["segments"]}
    source_identity = _file_identity(path)
    video_hash = chain._file_sha256(str(path))
    if _file_identity(path) != source_identity:
        raise ValueError("VIDEO source file changed during verification; retry with the completed scene.")
    workers = chain._png_export_worker_count(save_workers)

    def write(selected, marker):
        return _export_scene(chain, video, state, source, path, root, selected, config,
                             contracts, source_identity, video_hash, workers, verification,
                             reuse_existing, marker)

    return variants.export(chain, root, directory, state, config, _safe_path, _folder_lock, write)


def _export_scene(chain, video, state, source, path, root, directory, config, contracts,
                  source_identity, video_hash, workers, verification, reuse_existing, marker):
    index = int(state["index"])
    raw, delivered = int(source["raw_frames"]), int(source["delivered_frames"])
    bits = config["png_bit_depth"]
    embed_workflow = config["embed_workflow"]
    # The variant selector holds the destination lock throughout this call.
    record_path = _safe_path(root, directory / "export.json")
    previous = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else None
    clips = []
    if previous is not None:
        if (not isinstance(previous, dict) or previous.get("format") != FORMAT
                or previous.get("settings") != config):
            raise variants.SequenceConflict("PNG folder contains another sequence/settings.")
        clips = previous.get("clips")
        if not isinstance(clips, list) or not clips:
            raise ValueError("PNG sequence has invalid scene records; choose a new folder.")
        expected_scene, expected_frame = clips[0]["index"], config["first_frame_number"]
        for clip in clips:
            if (clip["index"] != expected_scene or clip["first_frame_number"] != expected_frame
                    or clip["source_contract"] != contracts.get(clip["index"])
                    or len(clip["files"]) != clip["delivered_frames"]):
                raise variants.SequenceConflict("PNG sequence branch/order changed.")
            for offset, item in enumerate(clip["files"]):
                if item["file"] != "frame_%08d.png" % (expected_frame + offset):
                    raise ValueError("PNG sequence contains an invalid frame address.")
                _safe_path(root, directory / item["file"])
                if not chain._png_export_file_unchanged(str(directory), item, verification):
                    raise variants.SequenceConflict("An existing PNG is missing or changed (scene %d: %s)." %
                                     (clip["index"], directory / item["file"]))
            expected_scene += 1
            expected_frame += clip["delivered_frames"]
    previous = transaction.recover(chain, root, directory, previous, config, contracts, _safe_path, _publish_frame)
    previous = _seed_variant_prefix(chain, root, directory, previous, marker, config, contracts)
    clips = previous["clips"] if previous is not None else []
    tracked = {item["file"] for clip in clips for item in clip["files"]}
    if any(p.name not in tracked for p in directory.glob("frame_*.png")):
        raise variants.SequenceConflict("PNG folder contains untracked frames.")
    session = state.get("png_export_session")
    if not reuse_existing and clips and session and not any(
            clip.get("export_session") == session for clip in clips):
        raise variants.SequenceConflict("Reuse is disabled; starting a fresh PNG sequence.")
    existing = next((clip for clip in clips if clip["index"] == index), None)
    if existing:
        same_session = bool(state.get("png_export_session")) and (
            existing.get("export_session") == state["png_export_session"])
        if (not reuse_existing and not same_session) or (existing["video_sha256"] != video_hash
                and not _matching_pixels(chain, path, raw, delivered, bits, existing, directory)):
            prefix = None
            if reuse_existing:
                prefix_record = deepcopy(previous)
                prefix_record["clips"] = [clip for clip in clips if clip["index"] < index]
                if prefix_record["clips"]:
                    prefix_record.update(frame_count=sum(c["delivered_frames"] for c in prefix_record["clips"]),
                                         last_scene=prefix_record["clips"][-1]["index"], complete=False)
                    prefix = {"directory": str(directory.relative_to(root)), "record": prefix_record}
            raise variants.SequenceConflict("This scene has different PNGs, or reuse is disabled.", prefix)
        if _file_identity(path) != source_identity:
            raise ValueError("VIDEO source file changed during verification; retry with the completed scene.")
        status = "reused PNG scene %d (%d-bit); VIDEO passed through unchanged -> %s" % (index, bits, directory)
        return {"ui": {"text": [status]}, "result": (str(directory), previous["frame_count"], status, "", video)}
    if clips and index != clips[-1]["index"] + 1:
        raise ValueError("PNG sequence has a scene gap. Resume at scene %d or choose a new folder." % (clips[-1]["index"] + 1))
    first = config["first_frame_number"] + sum(clip["delivered_frames"] for clip in clips)
    metadata = {"h3_run_name": state["run_name"], "h3_clip_index": str(index),
                "h3_png_bit_depth": str(bits), "h3_prompt": str(source.get("prompt") or "")}
    if embed_workflow:
        metadata.update(chain._archive_media_metadata(state["source_manifest"].get("archives")))
        metadata["h3_source_manifest"] = json.dumps(state["source_manifest"], ensure_ascii=False)
        metadata["h3_upscale_profile"] = json.dumps(state["profile_config"], ensure_ascii=False)
    progress = chain._png_export_progress(delivered)
    # Staging is private. A failed decode/write never commits a half-scene
    # or touches any earlier scene. Only PNG paths created below are undone.
    with transaction.staging(chain, directory, index) as stage:
        files, pending = [], set()
        count, width, height = 0, None, None
        pixels_hash = _pixel_hasher(bits)

        def completed(futures):
            for future in futures:
                files.append(future.result())
                chain._png_export_update_progress(progress, len(files), delivered)

        def write_frame(pixels, number, info):
            target = stage / ("frame_%08d.png" % number)
            chain._write_png(str(target), pixels, config["png_compression"], info)
            persistence.sync_file(target)
            return chain._png_export_file_record(str(target))

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="h3-video-png") as executor:
            for pixels in _scene_pixels(chain, path, raw, delivered, bits):
                if len(pending) >= workers:
                    done, pending = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
                    completed(done)
                height, width = pixels.shape[:2]
                _hash_pixels(pixels_hash, pixels)
                number = first + count
                pending.add(executor.submit(write_frame, pixels, number, metadata if count == 0 else {}))
                del pixels
                count += 1
            completed(pending)
        if len(files) != delivered or _file_identity(path) != source_identity:
            raise ValueError("VIDEO frame count or source file changed during PNG export; scene was not committed.")
        chain._png_export_check_interrupted()
        files.sort(key=lambda item: item["file"])
        clip = {"index": index, "id": source.get("id"), "source_contract": contracts[index],
                "export_session": state.get("png_export_session", ""),
                "source_revision": source.get("revision"), "video_sha256": video_hash,
                "pixel_sha256": pixels_hash.hexdigest(),
                "raw_frames": raw, "delivered_frames": delivered, "trim_frames": raw - delivered,
                "width": width, "height": height, "first_frame_number": first,
                "last_frame_number": first + delivered - 1, "files": files}
        record = {"format": FORMAT, "settings": config, "clips": clips + [clip],
                  "frame_count": first + delivered - config["first_frame_number"],
                  "complete": index == int(state["end_clip"]), "last_scene": index,
                  "source_manifest": state["source_manifest"], "audio": "preserved by the upscale segment saver"}
        transaction.publish(chain, root, directory, stage, previous, record, _safe_path, _publish_frame)
    status = "saved PNG scene %d: %d frames, RGB%d; %d sequence frames; VIDEO passed through unchanged -> %s" % (
        index, delivered, bits, record["frame_count"], directory)
    chain._LOG.info("H3 %s", status)
    return {"ui": {"text": [status]}, "result": (str(directory), record["frame_count"], status, "", video)}
