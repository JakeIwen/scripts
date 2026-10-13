#!/usr/bin/env python3
"""Add Tools > Interval Analysis, backed up and verified through BTT's API."""
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

NAMESPACE = uuid.UUID("f1080800-1aa2-484b-98dc-a9ff0a70b5b4")
SPEC = ToolsMenuItemSpec(
    description=__doc__,
    namespace=NAMESPACE,
    menu_name="Interval Analysis",
    identifier="interval-analysis",
    script_name="interval_analysis.py",
    runtime_name="bpm-python-path.txt",
    command_arguments=("--background",),
    missing_build_message="Build the updated Performance Audio.app first.",
    missing_runtime_message=(
        "Run /opt/homebrew/bin/python3 {media_tools}/scripts/setup_bpm_analysis.py first."
    ),
    payload_name="Interval Analysis Tools.json",
    generated_message="Generated: {payload}; run --apply to install the button.",
    backup_prefix="btt-before-interval-analysis-",
    conflict_message="Conflicting Interval Analysis item; no changes made.",
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
