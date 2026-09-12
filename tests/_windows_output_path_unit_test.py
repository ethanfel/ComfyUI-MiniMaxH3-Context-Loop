#!/usr/bin/env python3
"""Issue #45: mapped-drive and UNC spellings must share artifact identities."""

import ast
import contextlib
import ntpath
import os
import pathlib
import sys
import tempfile
import types
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import asset_store  # noqa: E402
import checkpoint_manager  # noqa: E402
import project_assets  # noqa: E402
import run_manager  # noqa: E402


def chain_paths(os_module, output_root, input_root):
    # Exercise the production path helpers without importing ComfyUI or models.
    names = {
        "_output_root", "_input_root", "_relative_output_path",
        "_absolute_output_path", "_png_export_checkpoint_cache_key",
    }
    source = ast.parse((ROOT / "chain_nodes.py").read_text(encoding="utf-8"))
    functions = [node for node in source.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {
        "os": os_module,
        "folder_paths": types.SimpleNamespace(
            get_output_directory=lambda: output_root,
            get_input_directory=lambda: input_root),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]),
                 str(ROOT / "chain_nodes.py"), "exec"), namespace)
    return namespace


def windows_os():
    # Use ntpath's real drive/UNC parsing on every test platform. Only the OS's
    # network-drive lookup is substituted: R: and S: map to the same share.
    paths = types.SimpleNamespace(**{
        name: getattr(ntpath, name) for name in dir(ntpath)
        if not name.startswith("__")})
    mappings = {
        "r:": r"\\mightyjobs\Resources",
        "s:": r"\\mightyjobs\Resources",
        "i:": r"\\mightyjobs\Inputs",
    }

    def realpath(value):
        absolute = ntpath.abspath(os.fspath(value))
        drive, tail = ntpath.splitdrive(absolute)
        return ntpath.normpath(mappings.get(drive.lower(), drive) + tail)

    paths.realpath = realpath
    return types.SimpleNamespace(path=paths, sep="\\")


def expect_escape(action):
    try:
        action()
    except ValueError as exc:
        assert "escapes" in str(exc), str(exc)
    else:
        raise AssertionError("An artifact outside the output root was accepted")


def windows_checks():
    win = windows_os()
    relative = r"h3_chains\demo\plan.json"
    mapped = r"R:\comfyui_output"
    unc = r"\\mightyjobs\Resources\comfyui_output"
    # This is the exact failing relpath combination from the issue report.
    try:
        ntpath.relpath(ntpath.join(unc, relative), mapped)
    except ValueError:
        pass
    else:
        raise AssertionError("The Windows mixed-mount reproduction did not fail")

    for output, inputs in (
        (mapped, r"I:\comfyui_input"),
        (unc, r"\\mightyjobs\Inputs\comfyui_input"),
        (r"D:\ComfyUI\output", r"D:\ComfyUI\input"),
    ):
        helpers = chain_paths(win, output, inputs)
        canonical_root = win.path.realpath(output)
        assert helpers["_output_root"]() == canonical_root
        assert helpers["_input_root"]() == win.path.realpath(inputs)
        for artifact in (ntpath.join(output, relative),
                         ntpath.join(canonical_root, relative)):
            saved = helpers["_relative_output_path"](artifact)
            assert saved == relative.replace("\\", "/")
            assert helpers["_absolute_output_path"](saved) == (
                ntpath.join(canonical_root, relative))
            assert helpers["_absolute_output_path"](artifact) == (
                ntpath.join(canonical_root, relative))
            cache_key = helpers.get("_png_export_checkpoint_cache_key")
            if cache_key is not None:
                assert cache_key(artifact) == relative.replace("\\", "/")
        expect_escape(lambda: helpers["_absolute_output_path"](r"..\outside.mp4"))
        expect_escape(lambda: helpers["_absolute_output_path"](
            r"\\another-server\share\outside.mp4"))
        expect_escape(lambda: helpers["_absolute_output_path"](
            canonical_root + r"_other\outside.mp4"))

        with contextlib.ExitStack() as stack:
            for module in (asset_store, run_manager, project_assets,
                           checkpoint_manager):
                stack.enter_context(patch.object(module, "os", win))
            assets = asset_store.RunAssetStore(output, inputs)
            runs = run_manager.RunArchiveManager(output, inputs)
            carousel = project_assets.ProjectAssetStore(inputs, output)
            checkpoints = checkpoint_manager.CheckpointGraphManager(output)
            for store in (assets, runs, carousel, checkpoints):
                assert store.output_root == canonical_root
            manifest_path = assets._manifest_path("demo")[0]
            assert ntpath.relpath(manifest_path, assets.output_root) == (
                r"h3_chains\demo\references\manifest.json")
            assert runs._run_dir("demo")[0] == ntpath.join(
                canonical_root, "h3_chains", "demo")
            project_dir = carousel._project_dir("demo")[0]
            assert ntpath.relpath(project_dir, carousel.input_root) == (
                r"h3_projects\demo")

    # A different mapping of the same share must still load saved relative data.
    remapped = chain_paths(win, r"S:\comfyui_output", r"I:\comfyui_input")
    assert remapped["_absolute_output_path"](relative) == ntpath.join(unc, relative)
    assert remapped["_relative_output_path"](ntpath.join(mapped, relative)) == relative.replace("\\", "/")


def filesystem_checks():
    # Real filesystem alias test also protects Linux symlinks and macOS paths.
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        physical = root / "physical output"
        physical.mkdir()
        alias = root / "configured output"
        try:
            alias.symlink_to(physical, target_is_directory=True)
        except OSError:
            print("Symlink check skipped: this host cannot create symlinks")
            return
        helpers = chain_paths(os, str(alias), str(alias))
        relative = os.path.join("h3_chains", "demo", "plan.json")
        artifact = physical / relative
        artifact.parent.mkdir(parents=True)
        artifact.write_text("{}", encoding="utf-8")
        assert helpers["_relative_output_path"](str(artifact)) == relative.replace(os.sep, "/")
        assert helpers["_relative_output_path"](str(alias / relative)) == relative.replace(os.sep, "/")
        assert pathlib.Path(helpers["_absolute_output_path"](relative)).samefile(artifact)


if __name__ == "__main__":
    windows_checks()
    filesystem_checks()
    print("Windows output paths: mapped drives, UNC, local disks, remapping, "
          "artifact round trips, asset roots, and containment checks pass")
