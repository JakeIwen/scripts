#!/usr/bin/env python3
"""Add one Convert Video button to Media > Tools through BTT's supported API."""

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid

from tools_menu_style import tools_button_config


ROOT = Path(__file__).resolve().parents[1]
MEDIA = "D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289"
TOOLS = "FC3F8235-F102-55C4-8432-B6ADFB0D9992"
NAMESPACE = uuid.UUID("c7b9d660-1b80-487a-8d1a-296d01be5a1c")


def uid(name):
    return str(uuid.uuid5(NAMESPACE, name)).upper()


def definition():
    resources = ROOT / "build/Performance Audio.app/Contents/Resources"
    engine = resources / "convert_video.py"
    if not engine.is_file():
        raise SystemExit("Build the updated Performance Audio.app containing convert_video.py first.")
    python = (resources / "python-path.txt").read_text().strip()
    config = tools_button_config("convert-video", "Convert Video")
    item_id = uid("button")
    return {"media_uuid": MEDIA, "tools_uuid": TOOLS, "item": {
        "BTTUUID": item_id, "BTTTriggerType": 773,
        "BTTTriggerClass": "BTTTriggerTypeFloatingMenu", "BTTEnabled": 1, "BTTEnabled2": 1,
        "BTTMenuName": "Convert Video", "BTTMenuConfig": config,
        "BTTMenuItemActions": [{
            "BTTUUID": uid("action"), "BTTTriggerParentUUID": item_id,
            "BTTTriggerClass": "BTTTriggerTypeFloatingMenu", "BTTEnabled": 1, "BTTOrder": 0,
            "BTTPredefinedActionType": 206, "BTTPredefinedActionName": "Run Shell Script / Task",
            "BTTShellTaskActionScript": shlex.join([python, str(engine), "--interactive", "--finder-selection",
                                                   "--background", "--notify", "--reveal"]),
            "BTTShellTaskActionConfig": "/bin/zsh:::-c:::-:::"}],
    }}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    payload = build / "Convert Video Tools.json"
    payload.write_text(json.dumps(definition(), indent=2) + "\n")
    if not args.apply and not args.inspect:
        print(f"Generated: {payload}. Run with --apply to install the button.")
        return
    result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", str(Path(__file__).with_suffix(".js")),
                             str(payload), str(build), "inspect" if args.inspect else "apply"])
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
