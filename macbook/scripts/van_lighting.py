#!/usr/bin/python3
"""Toggle a van light or power switch through the Pi dashboard's lighting API.

BTT's Media > Lighting buttons run this as blind toggles: the current state is
read from /api/lights and the opposite is sent to /api/lights/power, the same
endpoint the dashboard's buttons use. BTT discards a shell action's output, so a
failure is also shown as a macOS notification.

  van_lighting.py toggle switch.solder_flood
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from urllib import parse

from van_dashboard_http import DASHBOARD_URL, DashboardError, dashboard_json

DASHBOARD_URL_ENV = "VAN_LIGHTING_DASHBOARD_URL"
HTTP_TIMEOUT_SECONDS = 8.0
NOTIFICATION_TITLE = "Van lighting"
ON, OFF = "on", "off"


def _entities(payload: dict):
    try:
        groups = payload["lighting"]["groups"]
    except (KeyError, TypeError) as error:
        raise DashboardError("lights response has no groups") from error
    if not isinstance(groups, list):
        raise DashboardError("lights response groups are invalid")
    for group in groups:
        if not isinstance(group, dict):
            continue
        lights = group.get("lights")
        yield from [group.get("power_switch"), *(lights if isinstance(lights, list) else [])]


def target_state(payload: dict, target: str) -> str:
    """The on/off state of a light or power switch, by its entity id."""
    for entity in _entities(payload):
        if not isinstance(entity, dict) or entity.get("entity_id") != target:
            continue
        label = entity.get("label") or target
        if entity.get("available") is not True:
            raise DashboardError(f"{label} is unavailable")
        if entity.get("state") not in (ON, OFF):
            raise DashboardError(f"{label} reports state {entity.get('state')!r}")
        return entity["state"]
    raise DashboardError(f"{target} is not in the dashboard's lighting list")


def toggle(target: str, *, base_url: str, timeout: float) -> str:
    """Flip the target and return the state it was sent to."""
    base_url = base_url.rstrip("/")
    wanted = OFF if target_state(dashboard_json(f"{base_url}/api/lights", timeout=timeout), target) == ON else ON
    body = parse.urlencode({"target": target, "value": "true" if wanted == ON else "false"}).encode("ascii")
    dashboard_json(f"{base_url}/api/lights/power", data=body, timeout=timeout)
    return wanted


def notify(message: str) -> None:
    # The message travels as an argument, never as AppleScript source.
    script = ("on run argv", f'display notification (item 1 of argv) with title "{NOTIFICATION_TITLE}"',
              "end run")
    try:
        subprocess.run(["/usr/bin/osascript", *(part for line in script for part in ("-e", line)), message],
                       capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("toggle", help="turn the target off if it is on, otherwise on")
    command.add_argument("target", help="light or power-switch entity id, e.g. switch.solder_flood")
    args = parser.parse_args(argv)
    try:
        state = toggle(args.target, base_url=os.environ.get(DASHBOARD_URL_ENV, DASHBOARD_URL),
                       timeout=HTTP_TIMEOUT_SECONDS)
    except (DashboardError, OSError, ValueError) as error:
        detail = " ".join(f"{type(error).__name__}: {error}".split())
        print(f"{args.target}: toggle failed: {detail}", file=sys.stderr)
        notify(f"{args.target} toggle failed: {detail}")
        return 1
    print(f"{args.target}: turned {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
