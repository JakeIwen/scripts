#!/usr/bin/env python3
"""Add Tools > Rhythm Practice, backed up and verified through BTT's API."""
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

NAMESPACE = uuid.UUID("0ad78a97-1d4a-47c2-a8db-edf8872c820e")
SPEC = ToolsMenuItemSpec(
    description=__doc__,
    namespace=NAMESPACE,
    menu_name="Rhythm Practice",
    identifier="rhythm-practice",
    script_name="rhythm_practice.py",
    runtime_name="bpm-python-path.txt",
    command_arguments=("--finder-selection", "--background", "--open", "--notify"),
    missing_build_message="Build the updated Performance Audio.app first.",
    missing_runtime_message=(
        "Run /opt/homebrew/bin/python3 {media_tools}/scripts/setup_bpm_analysis.py first."
    ),
    payload_name="Rhythm Practice Tools.json",
    generated_message="Generated: {payload}; run --apply to install the button.",
    backup_prefix="btt-before-rhythm-practice-",
    conflict_message="Conflicting Rhythm Practice item; no changes made.",
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
