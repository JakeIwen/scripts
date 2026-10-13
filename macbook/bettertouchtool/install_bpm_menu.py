#!/usr/bin/env python3
"""Add Tools > BPM Over Time, backed up and verified through BTT's API."""
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

NAMESPACE = uuid.UUID("bd9b2717-342e-482e-b7a4-9abdc4177937")
SPEC = ToolsMenuItemSpec(
    description=__doc__,
    namespace=NAMESPACE,
    menu_name="BPM Over Time",
    identifier="bpm-over-time",
    script_name="bpm_over_time.py",
    runtime_name="bpm-python-path.txt",
    command_arguments=("--points", "60", "--finder-selection", "--background", "--open", "--notify"),
    missing_build_message="Build the updated Performance Audio.app first.",
    missing_runtime_message=(
        "Run /opt/homebrew/bin/python3 {media_tools}/scripts/setup_bpm_analysis.py first."
    ),
    payload_name="BPM Over Time Tools.json",
    generated_message="Generated: {payload}; run --apply to install the button.",
    backup_prefix="btt-before-bpm-over-time-",
    conflict_message="Conflicting BPM Over Time item; no changes made.",
)


def uid(name):
    return SPEC.uid(name)


def definition(media_tools=None):
    return build_definition(SPEC, MEDIA_TOOLS if media_tools is None else media_tools)


def main():
    run_installer(SPEC)


if __name__ == "__main__":
    main()
