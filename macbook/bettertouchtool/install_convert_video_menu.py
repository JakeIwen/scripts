#!/usr/bin/env python3
"""Add one Convert Video button to Media > Tools through BTT's supported API."""
import uuid

from btt_common import MEDIA_TOOLS
from tools_menu_item import (
    BUILD,
    MEDIA,
    TOOLS,
    ToolsMenuItemSpec,
    build_definition,
    run_installer,
)

NAMESPACE = uuid.UUID("c7b9d660-1b80-487a-8d1a-296d01be5a1c")
SPEC = ToolsMenuItemSpec(
    description=__doc__,
    namespace=NAMESPACE,
    menu_name="Convert Video",
    identifier="convert-video",
    script_name="convert_video.py",
    runtime_name="python-path.txt",
    command_arguments=("--interactive", "--finder-selection", "--background", "--notify", "--reveal"),
    missing_build_message="Build the updated Performance Audio.app containing convert_video.py first.",
    missing_runtime_message="App Python runtime is missing.",
    payload_name="Convert Video Tools.json",
    generated_message="Generated: {payload}. Run with --apply to install the button.",
    backup_prefix="btt-before-convert-video-",
    conflict_message="Conflicting Convert Video item; no changes made.",
    appearance_peer_uuid="554C41C9-C964-5875-B916-EF9D2DFB2004",
)


def uid(name):
    return SPEC.uid(name)


def definition(media_tools=None):
    return build_definition(SPEC, MEDIA_TOOLS if media_tools is None else media_tools)


def main():
    run_installer(SPEC)


if __name__ == "__main__":
    main()
