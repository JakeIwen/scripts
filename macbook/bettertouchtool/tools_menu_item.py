"""Shared definition and launcher for guarded Media > Tools item installers."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import subprocess
from typing import Optional
import uuid

from btt_common import MEDIA_TOOLS, ROOT
from tools_menu_style import tools_button_config

BUILD = ROOT / "macbook" / "build"
MEDIA = "D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289"
TOOLS = "FC3F8235-F102-55C4-8432-B6ADFB0D9992"


@dataclass(frozen=True)
class ToolsMenuItemSpec:
    description: str
    namespace: uuid.UUID
    menu_name: str
    identifier: str
    script_name: str
    runtime_name: str
    command_arguments: tuple[str, ...]
    missing_build_message: str
    missing_runtime_message: str
    payload_name: str
    generated_message: str
    backup_prefix: str
    conflict_message: str
    appearance_peer_uuid: Optional[str] = None

    def uid(self, name: str) -> str:
        return str(uuid.uuid5(self.namespace, name)).upper()

    def worker_options(self) -> dict:
        options = {
            "identifier": self.identifier,
            "menuName": self.menu_name,
            "backupPrefix": self.backup_prefix,
            "conflictMessage": self.conflict_message,
            "menuPath": f"Media > Tools > {self.menu_name}",
        }
        if self.appearance_peer_uuid:
            options["appearancePeerUUID"] = self.appearance_peer_uuid
        return options


def build_definition(spec: ToolsMenuItemSpec, media_tools=MEDIA_TOOLS) -> dict:
    media_tools = Path(media_tools)
    resources = media_tools / "build/Performance Audio.app/Contents/Resources"
    script = resources / spec.script_name
    runtime_file = resources / spec.runtime_name
    if not script.is_file() or not runtime_file.is_file():
        raise SystemExit(spec.missing_build_message)
    python = runtime_file.read_text().strip()
    if not Path(python).is_file():
        raise SystemExit(spec.missing_runtime_message.format(media_tools=media_tools))
    button = spec.uid("button")
    command = shlex.join([python, str(script), *spec.command_arguments])
    return {"media_uuid": MEDIA, "tools_uuid": TOOLS, "item": {
        "BTTUUID": button, "BTTTriggerType": 773, "BTTTriggerClass": "BTTTriggerTypeFloatingMenu",
        "BTTEnabled": 1, "BTTEnabled2": 1, "BTTMenuName": spec.menu_name,
        "BTTMenuConfig": tools_button_config(spec.identifier, spec.menu_name),
        "BTTMenuItemActions": [{"BTTUUID": spec.uid("action"), "BTTTriggerParentUUID": button,
            "BTTTriggerClass": "BTTTriggerTypeFloatingMenu", "BTTEnabled": 1, "BTTOrder": 0,
            "BTTPredefinedActionType": 206, "BTTPredefinedActionName": "Run Shell Script / Task",
            "BTTShellTaskActionScript": command,
            "BTTShellTaskActionConfig": "/bin/zsh:::-c:::-:::"}],
    }}


def _parser(spec: ToolsMenuItemSpec) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=spec.description)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--inspect", action="store_true")
    parser.add_argument("--refresh", action="store_true",
                        help="recreate the visible Media menu after verification")
    return parser


def run_installer(spec: ToolsMenuItemSpec) -> None:
    parser = _parser(spec)
    args = parser.parse_args()
    refresh = args.refresh
    if refresh and not args.apply:
        parser.error("--refresh requires --apply")
    os.umask(0o077)
    payload_data = build_definition(spec, MEDIA_TOOLS)
    BUILD.mkdir(exist_ok=True)
    payload = BUILD / spec.payload_name
    payload.write_text(json.dumps(payload_data, indent=2) + "\n")
    if not args.apply and not args.inspect:
        print(spec.generated_message.format(payload=payload))
        return
    directory = Path(__file__).resolve().parent
    worker = directory / "install_tools_menu_item.js"
    common = directory / "btt_common.js"
    result = subprocess.run([
        "/usr/bin/osascript", "-l", "JavaScript", str(worker), str(payload), str(common),
        str(BUILD), "inspect" if args.inspect else "apply", json.dumps(spec.worker_options()),
        "refresh" if refresh else "",
    ])
    raise SystemExit(result.returncode)
