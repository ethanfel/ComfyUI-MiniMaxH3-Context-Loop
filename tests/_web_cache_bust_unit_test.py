#!/usr/bin/env python3
"""Keep browser-cached .mjs helpers fresh across package and patch updates."""

import pathlib
import re


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
VERSION_MATCH = re.search(r'^version\s*=\s*"([^"]+)"', PROJECT, re.MULTILINE)
assert VERSION_MATCH, "pyproject.toml does not declare the project version"
VERSION = VERSION_MATCH.group(1)
MINIMUM_CACHE_VERSION = {
    "h3_policy_core.mjs": "0.6.6",
    "h3_project_asset_editor_core.mjs": "0.6.6",
    "h3_plan_restore_core.mjs": "0.6.6",
    "h3_socket_presentation_core.mjs": "0.6.6",
    "h3_chain_plan_core.mjs": "0.6.5",
    "h3_chain_review_core.mjs": "0.6.5",
    "h3_chain_cancel_reroll_core.mjs": "0.6.8",
    "h3_notification_stack_core.mjs": "0.6.8",
    "h3_chain_top_level_requeue_core.mjs": "0.6.8",
    "h3_chain_top_level_requeue_coordinator.mjs": "0.6.8",
    "h3_prompt_assistant_core.mjs": "0.6.5",
    "h3_chain_plan_studio_core.mjs": "0.6.3",
    "h3_checkpoint_manager_core.mjs": "0.6.6",
    "h3_prompt_companion_sync.mjs": "0.6.2",
    "h3_project_asset_sync_core.mjs": "0.6.2",
    "h3_reference_preview_core.mjs": "0.6.2",
    "h3_prompt_completion_core.mjs": "0.6.5",
    "h3_prompt_schema_core.mjs": "0.6.5",
    "h3_prompt_schema_ui.mjs": "0.6.5",
    "h3_rich_prompt_editor_core.mjs": "0.6.5",
    "h3_prompt_editor_settings_core.mjs": "0.6.5",
    "h3_prompt_marker_ui.mjs": "0.6.5",
}


def version_key(value):
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(value or "")), (
        "Invalid web cache token %r" % value)
    return tuple(int(part) for part in value.split("."))


IMPORT = re.compile(
    r'''["'](\./[^"']+\.mjs)(?:\?v=([^"']+))?["']''')


def main():
    imports = []
    for path in sorted((ROOT / "web").iterdir()):
        if path.suffix not in (".js", ".mjs"):
            continue
        source = path.read_text(encoding="utf-8")
        for relative, cache_version in IMPORT.findall(source):
            target = (path.parent / relative).resolve()
            assert target.is_file(), "%s imports missing %s" % (
                path.name, relative)
            minimum = max(
                version_key(VERSION),
                version_key(MINIMUM_CACHE_VERSION.get(target.name, VERSION)))
            assert version_key(cache_version) >= minimum, (
                "%s imports %s with stale cache token %r; expected at least %s"
                % (path.name, relative, cache_version, minimum))
            imports.append((path.name, relative))

    assert imports, "No production .mjs imports were checked."
    print("H3 web cache bust: %d .mjs imports meet their minimum "
          "fresh-cache versions" % len(imports))


if __name__ == "__main__":
    main()
