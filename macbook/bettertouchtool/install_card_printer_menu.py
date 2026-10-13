#!/usr/bin/env python3
"""Add one Temp Card Printer button to Media > Tools through BTT's supported API."""
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

NAMESPACE = uuid.UUID("213ea619-2101-43ff-bb80-56a0074ad243")
SPEC = ToolsMenuItemSpec(
    description=__doc__,
    namespace=NAMESPACE,
    menu_name="Temp Card Printer",
    identifier="temp-card-printer",
    script_name="temp_card_printer.py",
    runtime_name="python-path.txt",
    command_arguments=("--finder",),
    missing_build_message=(
        "Build the updated Performance Audio.app containing temp_card_printer.py first."
    ),
    missing_runtime_message="App Python runtime is missing.",
    payload_name="Temp Card Printer Tools.json",
    generated_message="Generated: {payload}. Run with --apply to install the button.",
    backup_prefix="btt-before-temp-card-printer-",
    conflict_message="Conflicting Temp Card Printer item; no changes made.",
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
