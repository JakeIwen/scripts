#!/usr/bin/env python3
"""Add Tools > BPM Over Time, backed up and verified through BTT's API."""
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
NAMESPACE = uuid.UUID("bd9b2717-342e-482e-b7a4-9abdc4177937")


def uid(name): return str(uuid.uuid5(NAMESPACE, name)).upper()


def definition():
    resources = ROOT / "build/Performance Audio.app/Contents/Resources"
    script = resources / "bpm_over_time.py"
    runtime_file = resources / "bpm-python-path.txt"
    if not script.is_file() or not runtime_file.is_file():
        raise SystemExit("Build the updated Performance Audio.app first.")
    python = runtime_file.read_text().strip()
    if not Path(python).is_file():
        raise SystemExit("Run /opt/homebrew/bin/python3 ~/dev/scripts/macbook/scripts/setup_bpm_analysis.py first.")
    button = uid("button")
    return {"media_uuid": MEDIA, "tools_uuid": TOOLS, "item": {
        "BTTUUID": button, "BTTTriggerType": 773, "BTTTriggerClass": "BTTTriggerTypeFloatingMenu",
        "BTTEnabled": 1, "BTTEnabled2": 1, "BTTMenuName": "BPM Over Time",
        "BTTMenuConfig": tools_button_config("bpm-over-time", "BPM Over Time"),
        "BTTMenuItemActions": [{"BTTUUID": uid("action"), "BTTTriggerParentUUID": button,
            "BTTTriggerClass": "BTTTriggerTypeFloatingMenu", "BTTEnabled": 1, "BTTOrder": 0,
            "BTTPredefinedActionType": 206, "BTTPredefinedActionName": "Run Shell Script / Task",
            "BTTShellTaskActionScript": shlex.join([python, str(script), "--points", "60", "--finder-selection",
                                                   "--background", "--open", "--notify"]),
            "BTTShellTaskActionConfig": "/bin/zsh:::-c:::-:::"}],
    }}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true")
    group.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    build = ROOT / "build"; build.mkdir(exist_ok=True)
    payload = build / "BPM Over Time Tools.json"
    payload.write_text(json.dumps(definition(), indent=2) + "\n")
    if not args.apply and not args.inspect:
        print(f"Generated: {payload}; run --apply to install the button.")
        return
    result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", str(Path(__file__).with_suffix(".js")),
                             str(payload), str(build), "inspect" if args.inspect else "apply"])
    raise SystemExit(result.returncode)


if __name__ == "__main__": main()
