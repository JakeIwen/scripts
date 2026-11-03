#!/usr/bin/python3
"""Control van lights and power switches through the Pi dashboard's lighting API.

BTT's Media > Lighting buttons run this. `toggle` is a blind toggle: the current
state is read from /api/lights and the opposite is sent to /api/lights/power, the
same endpoint the dashboard's buttons use; BTT discards a shell action's output,
so a failure is also shown as a macOS notification. `lights` and `set` serve the
colour control and always print one JSON object ({"ok": false, "error": ...} on
failure), with every light's state after the change.

  van_lighting.py toggle switch.solder_flood
  van_lighting.py lights
  van_lighting.py set light.wiz_dresser light.wiz_werkbench --hue 30 --saturation 60 --brightness 70
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from urllib import parse

from van_dashboard_http import DASHBOARD_URL, DashboardError, dashboard_json

DASHBOARD_URL_ENV = "VAN_LIGHTING_DASHBOARD_URL"
HTTP_TIMEOUT_SECONDS = 8.0
NOTIFICATION_TITLE = "Van lighting"
ON, OFF = "on", "off"
# The light fields `lights` and `set` pass through from /api/lights, plus their group.
LIGHT_FIELDS = ("entity_id", "label", "state", "available", "brightness", "hue", "saturation",
                "color_mode", "color_temp_kelvin", "min_color_temp_kelvin", "max_color_temp_kelvin",
                "supports_hue", "supports_color_temperature")


def _groups(payload: dict) -> list:
    try:
        groups = payload["lighting"]["groups"]
    except (KeyError, TypeError) as error:
        raise DashboardError("lights response has no groups") from error
    if not isinstance(groups, list):
        raise DashboardError("lights response groups are invalid")
    return [group for group in groups if isinstance(group, dict)]


def _lights(group: dict) -> list:
    lights = group.get("lights")
    return [light for light in lights if isinstance(light, dict)] if isinstance(lights, list) else []


def _entities(payload: dict):
    for group in _groups(payload):
        yield from [group.get("power_switch"), *_lights(group)]


def light_records(payload: dict) -> list[dict]:
    """Every light (not power switches) with its group, in dashboard order."""
    return [dict({field: light.get(field) for field in LIGHT_FIELDS},
                 group=group.get("id"), group_label=group.get("label"))
            for group in _groups(payload) for light in _lights(group)]


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


def _post(base_url: str, path: str, fields: dict, timeout: float) -> dict:
    body = parse.urlencode(fields).encode("ascii")
    return dashboard_json(f"{base_url}{path}", data=body, timeout=timeout)


def toggle(target: str, *, base_url: str, timeout: float) -> str:
    """Flip the target and return the state it was sent to."""
    base_url = base_url.rstrip("/")
    wanted = OFF if target_state(dashboard_json(f"{base_url}/api/lights", timeout=timeout), target) == ON else ON
    _post(base_url, "/api/lights/power", {"target": target, "value": "true" if wanted == ON else "false"}, timeout)
    return wanted


def _changes(args: argparse.Namespace) -> list[tuple[str, str, dict]]:
    """The requests `set` sends to each light, in order: power, colour, brightness."""
    changes = []
    if args.power is not None:
        changes.append(("/api/lights/power", "target", {"value": "true" if args.power else "false"}))
    if args.hue is not None:
        colour = {"hue": args.hue} if args.saturation is None else {"hue": args.hue, "saturation": args.saturation}
        changes.append(("/api/lights/hue", "entity", colour))
    if args.kelvin is not None:
        changes.append(("/api/lights/color-temperature", "entity", {"kelvin": args.kelvin}))
    if args.brightness is not None:
        changes.append(("/api/lights/brightness", "entity", {"brightness": args.brightness}))
    return changes


def set_lights(entities: list[str], changes: list[tuple[str, str, dict]], *, base_url: str,
               timeout: float) -> dict:
    """Send every change to each light; each reply carries the status after it.

    changes is never empty: main refuses a set with nothing to change.
    """
    base_url = base_url.rstrip("/")
    for entity in entities:
        for path, key, fields in changes:
            payload = _post(base_url, path, {key: entity, **fields}, timeout)
    return payload


def notify(message: str) -> None:
    # The message travels as an argument, never as AppleScript source.
    command = ["/usr/bin/osascript", "-e", "on run argv",
               "-e", f'display notification (item 1 of argv) with title "{NOTIFICATION_TITLE}"',
               "-e", "end run", message]
    try:
        subprocess.run(command, capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def _bounded(low: int, high: int):
    def parse_value(text: str) -> int:
        value = int(text)
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"must be from {low} to {high}")
        return value
    return parse_value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    # toggle reports for a person (and BTT's hidden output); lights and set print JSON.
    toggle_command = commands.add_parser("toggle", help="turn the target off if it is on, otherwise on")
    toggle_command.add_argument("target", help="light or power-switch entity id, e.g. switch.solder_flood")
    toggle_command.set_defaults(json_output=False)
    commands.add_parser("lights", help="print every light's state as JSON").set_defaults(json_output=True)
    set_command = commands.add_parser("set", help="change one or more lights, then print their state as JSON")
    set_command.set_defaults(json_output=True)
    set_command.add_argument("entities", nargs="+", metavar="entity", help="light entity id, e.g. light.wiz_dresser")
    power = set_command.add_mutually_exclusive_group()
    power.add_argument("--on", dest="power", action="store_const", const=True)
    power.add_argument("--off", dest="power", action="store_const", const=False)
    colour = set_command.add_mutually_exclusive_group()
    colour.add_argument("--hue", type=_bounded(0, 360), help="colour hue in degrees")
    colour.add_argument("--kelvin", type=_bounded(2000, 7000), help="white colour temperature")
    set_command.add_argument("--saturation", type=_bounded(0, 100), help="colour saturation %% (with --hue)")
    set_command.add_argument("--brightness", type=_bounded(1, 100), help="brightness %%")
    return parser


def _run(args: argparse.Namespace, base_url: str):
    """toggle returns the new state; lights and set return the JSON result."""
    if args.command == "toggle":
        return toggle(args.target, base_url=base_url, timeout=HTTP_TIMEOUT_SECONDS)
    if args.command == "lights":
        payload = dashboard_json(f"{base_url.rstrip('/')}/api/lights", timeout=HTTP_TIMEOUT_SECONDS)
    else:
        payload = set_lights(args.entities, args.changes, base_url=base_url, timeout=HTTP_TIMEOUT_SECONDS)
    return {"ok": True, "lights": light_records(payload)}


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "set":
        if args.saturation is not None and args.hue is None:
            parser.error("--saturation needs --hue")
        args.changes = _changes(args)
        if not args.changes:
            parser.error("set needs at least one of --on/--off, --hue, --kelvin, --brightness")
    try:
        result = _run(args, os.environ.get(DASHBOARD_URL_ENV, DASHBOARD_URL))
    except (DashboardError, OSError, ValueError) as error:
        detail = " ".join(f"{type(error).__name__}: {error}".split())
        if args.json_output:
            print(json.dumps({"ok": False, "error": detail}))
        else:
            print(f"{args.target}: toggle failed: {detail}", file=sys.stderr)
            notify(f"{args.target} toggle failed: {detail}")
        return 1
    print(json.dumps(result) if args.json_output else f"{args.target}: turned {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
