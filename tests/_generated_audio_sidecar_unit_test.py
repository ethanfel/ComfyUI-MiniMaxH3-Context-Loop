#!/usr/bin/env python3
"""Standalone regression test for preserving H3 audio beside source-audio finals."""

from source_audio_fixtures import bind_manifest_audio

import importlib.util
import pathlib
import sys
import tempfile
import types
import wave

import torch


ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "h3_generated_audio_sidecar_unit"

folder_paths = types.ModuleType("folder_paths")
folder_paths.get_output_directory = lambda: folder_paths.output_directory
folder_paths.get_temp_directory = lambda: folder_paths.output_directory
folder_paths.get_input_directory = lambda: folder_paths.output_directory
folder_paths.get_annotated_filepath = lambda value: str(value)
folder_paths.output_directory = str(ROOT)
sys.modules["folder_paths"] = folder_paths

package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package

shared_nodes = types.ModuleType(PACKAGE + ".nodes")
shared_nodes.MiniMaxH3MotionContext = object
shared_nodes._claim_inline_patch_ownership = lambda _conditioning=None: "test patch owner"
shared_nodes._prepare_native_guide_conditioning = lambda *args: None
shared_nodes._resize = lambda *args: None
shared_nodes._streams_from_latent = lambda *args: None
sys.modules[shared_nodes.__name__] = shared_nodes

spec = importlib.util.spec_from_file_location(
    PACKAGE + ".chain_nodes", ROOT / "chain_nodes.py")
chain = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = chain
spec.loader.exec_module(chain)


def audio(value):
    return {
        "waveform": torch.full(
            (1, 2, round(5 / chain.FPS * 8000)), value,
            dtype=torch.float32),
        "sample_rate": 8000,
    }


def main():
    assemble_inputs = chain.MiniMaxH3ChainAssemble.INPUT_TYPES()
    assert assemble_inputs["optional"]["copy_to_output"][1]["default"] is False
    assert assemble_inputs["optional"]["output_subfolder"][1]["default"] == ""
    with tempfile.TemporaryDirectory() as tempdir:
        folder_paths.output_directory = tempdir
        segment_path = pathlib.Path(
            tempdir, "h3_chains", "sidecar", "segments", "clip_0001.mp4")
        segment_path.parent.mkdir(parents=True)
        segment_path.write_bytes(b"fake H.264 segment")
        manifest = {
            "format": "h3_chain_manifest_v3",
            "run_name": "sidecar",
            "compatibility": {"audio_mode": "source_track"},
            "clip_count": 1,
            "total_delivered_frames": 5,
            "segments": [{
                "index": 1,
                "segment": chain._relative_output_path(str(segment_path)),
                "delivered_frames": 5,
            }],
        }
        generated = audio(0.25)
        source = audio(0.0)
        bind_manifest_audio(chain, manifest, source)
        muxed_pcm = []

        chain._validate_manifest = lambda value: value["segments"]
        chain._validate_prelude = lambda _value: None
        chain._generated_audio = lambda _value, **_kwargs: generated
        chain._manifest_media_metadata = lambda _value: {}
        original_which = chain.shutil.which
        original_run_ffmpeg = chain._run_ffmpeg
        original_pyav_concat = chain._pyav_concat_video
        original_pyav_mux = chain._pyav_mux_audio
        chain.shutil.which = lambda executable: (
            "/fake/ffmpeg" if executable == "ffmpeg"
            else original_which(executable))

        def fake_ffmpeg(command, timeout_seconds=None):
            del timeout_seconds
            if command[-1] == "-version":
                return
            output = pathlib.Path(command[-1])
            if output.name == ".final.tmp.mp4":
                with wave.open(command[5], "rb") as selected_audio:
                    muxed_pcm.append(selected_audio.readframes(
                        selected_audio.getnframes()))
            output.write_bytes(b"assembled video")

        chain._run_ffmpeg = fake_ffmpeg
        try:
            result = chain.MiniMaxH3ChainAssemble().assemble(manifest, "plan", "source_final", 96, copy_to_output=True, output_subfolder="published/finals", source_timeline=chain._make_source_timeline(source_audio=source))
            manifest["compatibility"]["audio_mode"] = "generated_audio"
            generated_result = chain.MiniMaxH3ChainAssemble().assemble(
                manifest, "plan", "generated_final", 96)
            manifest["compatibility"]["audio_mode"] = "source_track"

            chain.shutil.which = lambda executable: (
                "/broken/ffmpeg" if executable == "ffmpeg"
                else original_which(executable))

            def unusable_ffmpeg(*_args, **_kwargs):
                raise RuntimeError(
                    "ffmpeg failed (3221225785 / 0xC0000139)")

            chain._run_ffmpeg = unusable_ffmpeg
            chain._pyav_concat_video = (
                lambda _sources, _frames, path, _metadata:
                pathlib.Path(path).write_bytes(b"PyAV joined video"))
            chain._pyav_mux_audio = (
                lambda _video, _audio, path, _bitrate, _frames:
                pathlib.Path(path).write_bytes(b"PyAV video with audio"))
            fallback = chain.MiniMaxH3ChainAssemble().assemble(manifest, "source", "broken_ffmpeg_fallback", 96, source_timeline=chain._make_source_timeline(source_audio=source))
        finally:
            chain.shutil.which = original_which
            chain._run_ffmpeg = original_run_ffmpeg
            chain._pyav_concat_video = original_pyav_concat
            chain._pyav_mux_audio = original_pyav_mux
            chain._FFMPEG_PROBE_CACHE.clear()

        final_path = pathlib.Path(result["result"][0])
        sidecar_path = final_path.with_suffix(".generated.wav")
        output_copy = pathlib.Path(
            tempdir, "published", "finals", "source_final.mp4")
        assert final_path.is_file()
        assert output_copy.read_bytes() == final_path.read_bytes()
        assert result["ui"]["images"] == [
            chain._video_output_item(str(output_copy))]
        assert result["ui"]["animated"] == (True,)
        assert sidecar_path.is_file()
        assert len(muxed_pcm) == 2
        assert not any(muxed_pcm[0])
        assert any(muxed_pcm[1])
        assert pathlib.Path(generated_result["result"][0]).is_file()
        with wave.open(str(sidecar_path), "rb") as generated_audio:
            assert generated_audio.getframerate() == 8000
            assert generated_audio.getnchannels() == 2
            assert generated_audio.getnframes() == round(5 / chain.FPS * 8000)
            assert any(generated_audio.readframes(generated_audio.getnframes()))
        assert "generated audio ->" in result["ui"]["text"][0]
        assert "output copy ->" in result["ui"]["text"][0]
        second_copy = pathlib.Path(chain._copy_final_to_output(
            str(final_path), "published/finals"))
        assert second_copy.name == "source_final_001.mp4"
        assert second_copy.read_bytes() == final_path.read_bytes()
        try:
            chain._copy_final_to_output(str(final_path), "../escape")
        except ValueError as exc:
            assert "cannot contain '..'" in str(exc)
        else:
            raise AssertionError("output copy accepted a parent traversal")
        assert pathlib.Path(fallback["result"][0]).is_file()
        assert fallback["ui"]["images"] == [
            chain._video_output_item(fallback["result"][0])]
        assert fallback["ui"]["animated"] == (True,)
        assert "PyAV fallback" in fallback["ui"]["text"][0]

    print("H3 generated audio sidecar: source mux remains unchanged and H3 "
          "WAV is preserved")


if __name__ == "__main__":
    main()
