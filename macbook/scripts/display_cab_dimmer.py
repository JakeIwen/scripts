#!/usr/bin/env python3
"""Dim lit cab lights when a Lenovo Q24h-10 display is plugged in.

Runs as the LaunchAgent com.jacobr.display-cab-dimmer from its own venv. Display
callbacks are settled before the online display set is compared, with polling as
a fallback. The starting display set is only a baseline and never dims lights.

Settings are declared in shared/settings/display-cab-dimmer.json and saved in
~/.config/jacobr/display-cab-dimmer.json. Valid edits are applied while running.

  display_cab_dimmer.py             watch for a monitor plug-in
  display_cab_dimmer.py --list      print online displays and target matches
  display_cab_dimmer.py --dim-now   dim the currently lit cab lights once
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import sys
import time
from typing import Callable
from urllib import parse, request

import tool_settings

TOOL = "display-cab-dimmer"
MONITOR_NAME = "Q24h-10"
DASHBOARD_URL = "http://vanpi.lan:8788"
DASHBOARD_URL_ENV = "DISPLAY_CAB_DIMMER_DASHBOARD_URL"
CAB_GROUP = "cab"
HTTP_TIMEOUT_SECONDS = 8.0
FALLBACK_POLL_SECONDS = 20.0
RUN_LOOP_SECONDS = 1.0
WAKE_SETTLE_SECONDS = 5.0
BEGIN_CONFIGURATION_FLAG = 1
_DISPLAY_NUMBER_SUFFIX = re.compile(r" \(\d+\)$")
DisplayKey = tuple[int, int, int]


@dataclass(frozen=True)
class DisplayInfo:
    display_id: int
    name: str
    built_in: bool
    vendor: int
    model: int
    unit: int
    serial: int = 0

    @property
    def key(self) -> DisplayKey:
        return (self.vendor, self.model, self.unit)

    @property
    def matches_target(self) -> bool:
        return not self.built_in and display_name_matches(self.name)


def display_name_matches(name: str) -> bool:
    """macOS adds a numeric suffix when identical displays are connected."""
    return _DISPLAY_NUMBER_SUFFIX.sub("", name) == MONITOR_NAME


def matching_snapshot(displays: list[DisplayInfo]) -> frozenset[DisplayKey]:
    return frozenset(display.key for display in displays if display.matches_target)


class PlugTracker:
    """Decide whether a settled matching-display snapshot is a plug-in event."""

    def __init__(self, initial_snapshot: frozenset[DisplayKey]):
        self._baseline = frozenset(initial_snapshot)
        self._deadline: float | None = None
        self._asleep = False

    def schedule(self, now: float, settle_seconds: float) -> None:
        if self._asleep:
            return
        candidate = now + settle_seconds
        self._deadline = candidate if self._deadline is None else max(self._deadline, candidate)

    def will_sleep(self) -> None:
        self._asleep = True
        self._deadline = None

    def did_wake(self, now: float, settle_seconds: float) -> None:
        self._asleep = False
        self.schedule(now, max(settle_seconds, WAKE_SETTLE_SECONDS))

    def due(self, now: float) -> bool:
        return not self._asleep and self._deadline is not None and now >= self._deadline

    def evaluate(
        self,
        snapshot: frozenset[DisplayKey],
        now: float,
        *,
        fallback: bool = False,
    ) -> bool:
        """Return true once for any newly present targets after settling."""
        if self._asleep:
            return False
        if self._deadline is not None:
            if now < self._deadline:
                return False
            self._deadline = None
        elif not fallback:
            return False
        current = frozenset(snapshot)
        plugged_in = bool(current - self._baseline)
        self._baseline = current
        return plugged_in

    def abandon_pending_check(self) -> None:
        self._deadline = None


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def online_displays() -> list[DisplayInfo]:
    """Read online displays without importing PyObjC until macOS glue is used."""
    import Quartz
    from AppKit import NSScreen

    error, display_ids, _count = Quartz.CGGetOnlineDisplayList(16, None, None)
    if error != 0:
        raise RuntimeError(f"CGGetOnlineDisplayList failed with error {error}")
    names = {
        int(screen.deviceDescription()["NSScreenNumber"]): str(screen.localizedName())
        for screen in NSScreen.screens()
    }
    return [
        DisplayInfo(
            int(display_id),
            names.get(int(display_id), "<unknown>"),
            bool(Quartz.CGDisplayIsBuiltin(display_id)),
            int(Quartz.CGDisplayVendorNumber(display_id)),
            int(Quartz.CGDisplayModelNumber(display_id)),
            int(Quartz.CGDisplayUnitNumber(display_id)),
            int(Quartz.CGDisplaySerialNumber(display_id)),
        )
        for display_id in display_ids
    ]


def list_displays() -> None:
    for display in online_displays():
        kind = "built-in" if display.built_in else "external"
        match = "yes" if display.matches_target else "no"
        print(
            f"id={display.display_id} name={display.name!r} {kind} "
            f"vendor={display.vendor} model={display.model} unit={display.unit} "
            f"matches={match}"
        )


class DashboardError(RuntimeError):
    """The dashboard returned data that cannot confirm a completed operation."""


def _dashboard_json(url: str, *, data: bytes | None = None, timeout: float) -> dict:
    headers = {"Content-Type": "application/x-www-form-urlencoded"} if data is not None else {}
    operation = request.Request(url, data=data, headers=headers)
    with request.urlopen(operation, timeout=timeout) as response:
        if response.status != 200:
            raise DashboardError(f"HTTP {response.status}")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        detail = payload.get("error", "invalid response") if isinstance(payload, dict) else "invalid response"
        raise DashboardError(str(detail))
    return payload


def _cab_lights(payload: dict) -> list[dict]:
    try:
        groups = payload["lighting"]["groups"]
    except (KeyError, TypeError) as error:
        raise DashboardError("lights response has no groups") from error
    if not isinstance(groups, list):
        raise DashboardError("lights response groups are invalid")
    for group in groups:
        if isinstance(group, dict) and group.get("id") == CAB_GROUP:
            lights = group.get("lights")
            if not isinstance(lights, list):
                raise DashboardError("cab lights response is invalid")
            return lights
    raise DashboardError("cab light group is unavailable")


def dim_cab_lights(
    brightness: int,
    *,
    dashboard_url: str | None = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
) -> int:
    """Dim currently lit cab lights once, logging and swallowing every failure."""
    base_url = dashboard_url or os.environ.get(DASHBOARD_URL_ENV, DASHBOARD_URL)
    changed = 0
    try:
        payload = _dashboard_json(f"{base_url.rstrip('/')}/api/lights", timeout=timeout)
        lights = _cab_lights(payload)
        for light in lights:
            if not isinstance(light, dict) or light.get("state") != "on":
                continue
            if light.get("brightness") == brightness:
                continue
            entity_id = light.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id:
                raise DashboardError("cab light has no entity id")
            body = parse.urlencode({"entity": entity_id, "brightness": brightness}).encode("ascii")
            _dashboard_json(
                f"{base_url.rstrip('/')}/api/lights/brightness",
                data=body,
                timeout=timeout,
            )
            changed += 1
            log(f"{entity_id}: brightness set to {brightness}%")
        log(f"cab dimming complete: changed {changed} light(s) to {brightness}%")
    except Exception as error:
        log(f"cab dimming skipped: {type(error).__name__}: {error}")
    return changed


def _reload_settings(reloader, settings: dict, ignored_error: str | None) -> tuple[dict, str | None]:
    result = reloader.poll()
    if result.error is not None:
        if result.error != ignored_error:
            log(f"settings ignored, keeping {settings}: {result.error}")
        return settings, result.error
    if result.changed:
        log(
            f"settings reloaded; brightness {result.values['brightness']}%; "
            f"settle_seconds {result.values['settle_seconds']:g}"
        )
    return result.values, None


def watch() -> None:
    import Quartz
    from AppKit import NSWorkspace
    from Foundation import NSObject

    reloader = tool_settings.Reloader(TOOL)
    settings = reloader.poll().values
    initial_snapshot = matching_snapshot(online_displays())
    tracker = PlugTracker(initial_snapshot)
    runtime = {"settle_seconds": float(settings["settle_seconds"])}

    def reconfigured(_display, flags, _info):
        if int(flags) & BEGIN_CONFIGURATION_FLAG:
            return
        tracker.schedule(time.monotonic(), runtime["settle_seconds"])

    class WorkspaceObserver(NSObject):
        def sleep_(self, _notification):
            tracker.will_sleep()

        def wake_(self, _notification):
            tracker.did_wake(time.monotonic(), runtime["settle_seconds"])

    observer = WorkspaceObserver.alloc().init()
    notifications = NSWorkspace.sharedWorkspace().notificationCenter()
    notifications.addObserver_selector_name_object_(
        observer, "sleep:", "NSWorkspaceWillSleepNotification", None
    )
    notifications.addObserver_selector_name_object_(
        observer, "wake:", "NSWorkspaceDidWakeNotification", None
    )
    callback_error = Quartz.CGDisplayRegisterReconfigurationCallback(reconfigured, None)
    if callback_error != 0:
        notifications.removeObserver_(observer)
        raise RuntimeError(f"display callback registration failed with error {callback_error}")

    ignored_error = None
    next_poll = time.monotonic() + FALLBACK_POLL_SECONDS
    log(f"watching for {MONITOR_NAME}; baseline has {len(initial_snapshot)} matching display(s)")
    try:
        while True:
            Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, RUN_LOOP_SECONDS, True)
            settings, ignored_error = _reload_settings(reloader, settings, ignored_error)
            runtime["settle_seconds"] = float(settings["settle_seconds"])
            now = time.monotonic()
            fallback = now >= next_poll
            if not tracker.due(now) and not fallback:
                continue
            if fallback:
                next_poll = now + FALLBACK_POLL_SECONDS
            try:
                snapshot = matching_snapshot(online_displays())
            except Exception as error:
                tracker.abandon_pending_check()
                log(f"display check skipped: {type(error).__name__}: {error}")
                continue
            if tracker.evaluate(snapshot, now, fallback=fallback):
                dim_cab_lights(int(settings["brightness"]))
    finally:
        Quartz.CGDisplayRemoveReconfigurationCallback(reconfigured, None)
        notifications.removeObserver_(observer)


def main(argv: list[str]) -> int:
    if argv == ["--list"]:
        list_displays()
        return 0
    if argv == ["--dim-now"]:
        settings = tool_settings.load(TOOL)
        dim_cab_lights(int(settings["brightness"]))
        return 0
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    tool_settings.load(TOOL)  # fail at startup, not mid-run, on bad saved settings
    watch()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
