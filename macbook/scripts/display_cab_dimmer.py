#!/usr/bin/env python3
"""Dim lit cab lights when a Lenovo Q24h-10 display is plugged in.

Runs as the LaunchAgent com.jacobr.display-cab-dimmer from its own venv. Display
callbacks are settled before the online display set is compared, with polling as
a fallback. The starting display set is only a baseline and never dims lights.

Settings are declared in shared/settings/display-cab-dimmer.json and saved in
~/.config/jacobr/display-cab-dimmer.json. Valid edits are applied while running.
Activity is recorded in ~/.local/state/display-cab-dimmer/state.json.

  display_cab_dimmer.py                    watch for a monitor plug-in
  display_cab_dimmer.py --list             print online displays and target matches
  display_cab_dimmer.py --dim-now          dim the currently lit cab lights once
  display_cab_dimmer.py --status [--json]  print recorded automation status
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from urllib import parse, request

import tool_automation
import tool_settings
from cab_dimmer.displays import online_displays
from cab_dimmer.model import (
    DimOutcome, DimResult, DisplayInfo, MIRROR_NAME, MONITOR_NAME, PlugTracker, RunTrigger,
    display_name_matches, matching_monitor_names, matching_snapshot,
)
from cab_dimmer.state_store import STATE_DIR_ENV, read_state, update_state

TOOL = "display-cab-dimmer"
DASHBOARD_URL = "http://vanpi.lan:8788"
DASHBOARD_URL_ENV = "DISPLAY_CAB_DIMMER_DASHBOARD_URL"
CAB_GROUP = "cab"
HTTP_TIMEOUT_SECONDS = 8.0
FALLBACK_POLL_SECONDS = 20.0
RUN_LOOP_SECONDS = 1.0
BEGIN_CONFIGURATION_FLAG = 1
STATUS_OK, STATUS_IDLE, STATUS_UNKNOWN, STATUS_WARN, _STATUS_ERROR = tool_automation.STATUS_LEVELS


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


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


def _one_line_error(error: Exception) -> str:
    return " ".join(f"{type(error).__name__}: {error}".split())


def dim_cab_lights(
    brightness: int,
    *,
    dashboard_url: str | None = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
) -> DimResult:
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
        detail = f"changed {changed} light(s) to {brightness}%"
        log(f"cab dimming complete: {detail}")
        outcome = DimOutcome.DIMMED if changed else DimOutcome.NOTHING_TO_DO
        return DimResult(outcome, changed, detail)
    except Exception as error:
        detail = _one_line_error(error)
        log(f"cab dimming skipped: {detail}")
        return DimResult(DimOutcome.FAILED, changed, detail)


def _last_run(trigger: RunTrigger, at: float, result: DimResult) -> dict:
    return {
        "trigger": trigger.value,
        "at": at,
        "outcome": result.outcome.value,
        "changed": result.changed,
        "detail": result.detail,
    }


def handle_plug_in(
    settings: dict,
    *,
    dashboard_url: str | None = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    state_dir: Path | str | None = None,
    now: float | None = None,
) -> DimResult:
    """Apply the enabled decision and record one detected plug-in."""
    at = time.time() if now is None else now
    if settings["enabled"]:
        result = dim_cab_lights(
            int(settings["brightness"]), dashboard_url=dashboard_url, timeout=timeout
        )
    else:
        result = DimResult(DimOutcome.DISABLED, 0, "disabled; lights left unchanged")
        log("cab dimming skipped: disabled")
    update_state(
        {"last_plug_in": at, "last_run": _last_run(RunTrigger.PLUG_IN, at, result)},
        state_dir,
    )
    return result


def run_manual(
    settings: dict,
    *,
    dashboard_url: str | None = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    state_dir: Path | str | None = None,
    now: float | None = None,
) -> DimResult:
    at = time.time() if now is None else now
    result = dim_cab_lights(
        int(settings["brightness"]), dashboard_url=dashboard_url, timeout=timeout
    )
    update_state({"last_run": _last_run(RunTrigger.MANUAL, at, result)}, state_dir)
    return result


def _pid_is_alive(value) -> bool:
    if type(value) is not int or value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _epoch(value) -> float | None:
    if type(value) in (int, float) and value >= 0:
        return float(value)
    return None


def _count(amount: int, noun: str) -> str:
    return f"{amount} {noun}" if amount == 1 else f"{amount} {noun}s"


def _last_run_value(last_run) -> tuple[str, float | None, str | None]:
    if not isinstance(last_run, dict):
        return "Never", None, None
    try:
        outcome = DimOutcome(last_run.get("outcome"))
        trigger = RunTrigger(last_run.get("trigger"))
    except ValueError:
        return "Unknown", _epoch(last_run.get("at")), STATUS_WARN
    changed = last_run.get("changed")
    changed = changed if type(changed) is int and changed >= 0 else 0
    suffix = f"({trigger.value})"
    if outcome is DimOutcome.DIMMED:
        value = f"Dimmed {_count(changed, 'light')} {suffix}"
    elif outcome is DimOutcome.NOTHING_TO_DO:
        value = f"Nothing to dim {suffix}"
    elif outcome is DimOutcome.DISABLED:
        value = f"Skipped, disabled {suffix}"
    else:
        detail = str(last_run.get("detail") or "unknown error")
        value = f"Failed: {detail[:450]} {suffix}"
    level = STATUS_WARN if outcome is DimOutcome.FAILED else None
    return value, _epoch(last_run.get("at")), level


def automation_status(
    settings: dict | None = None,
    *,
    state_dir: Path | str | None = None,
) -> dict:
    """Build status without importing macOS frameworks or contacting the dashboard."""
    values = tool_settings.load(TOOL) if settings is None else settings
    state = read_state(state_dir)
    agent = state.get("agent") if isinstance(state.get("agent"), dict) else {}
    pid = agent.get("pid")
    running = _pid_is_alive(pid)
    monitors = state.get("monitors")
    if not isinstance(monitors, list) or not all(isinstance(item, str) for item in monitors):
        monitors = []
    if not state:
        level, summary = STATUS_UNKNOWN, "No activity recorded yet"
    elif not running:
        level, summary = STATUS_WARN, "Agent is not running"
    elif not values["enabled"]:
        level = STATUS_IDLE
        summary = "Disabled; plug-ins are recorded but the lights are left alone"
    else:
        level, summary = STATUS_OK, f"Watching {_count(len(monitors), 'monitor')}"
    last_run_value, last_run_at, last_run_level = _last_run_value(state.get("last_run"))
    last_plug_in = _epoch(state.get("last_plug_in"))
    fields = [
        tool_automation.status_field("Enabled", "Yes" if values["enabled"] else "No"),
        tool_automation.status_field("Brightness", f"{int(values['brightness'])}%"),
        tool_automation.status_field(
            "Monitors", ", ".join(monitors) if monitors else "None", _epoch(state.get("monitors_at"))
        ),
        tool_automation.status_field("Last plug-in", time=last_plug_in)
        if last_plug_in is not None
        else tool_automation.status_field("Last plug-in", "Never"),
        tool_automation.status_field("Last run", last_run_value, last_run_at, last_run_level),
        tool_automation.status_field(
            "Agent", f"Running, pid {pid}" if running else "Not running", _epoch(agent.get("started_at"))
        ),
    ]
    return tool_automation.status_record(level, summary, fields)


def print_status(record: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(record, separators=(",", ":"), allow_nan=False))
        return
    print(f"{record['summary']} [{record['level']}]")
    for field in record["fields"]:
        value = field["value"] or ""
        if field["time"] is not None:
            rendered_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(field["time"]))
            value = f"{value} ({rendered_time})" if value else rendered_time
        suffix = f" [{field['level']}]" if field["level"] is not None else ""
        print(f"{field['label']}: {value}{suffix}")


def _reload_settings(reloader, settings: dict, ignored_error: str | None) -> tuple[dict, str | None]:
    result = reloader.poll()
    if result.error is not None:
        if result.error != ignored_error:
            log(f"settings ignored, keeping {settings}: {result.error}")
        return settings, result.error
    if result.changed:
        enabled = "on" if result.values["enabled"] else "off"
        log(
            f"settings reloaded; enabled {enabled}; brightness {result.values['brightness']}%; "
            f"settle_seconds {result.values['settle_seconds']:g}"
        )
    return result.values, None


def watch() -> None:
    import Quartz
    from AppKit import NSWorkspace
    from Foundation import NSObject

    reloader = tool_settings.Reloader(TOOL)
    settings = reloader.poll().values
    initial_displays = online_displays()
    initial_snapshot = matching_snapshot(initial_displays)
    monitors = matching_monitor_names(initial_displays)
    tracker = PlugTracker(initial_snapshot)
    runtime = {"settle_seconds": float(settings["settle_seconds"])}
    started_at = time.time()
    update_state(
        {
            "agent": {"pid": os.getpid(), "started_at": started_at},
            "monitors": monitors,
            "monitors_at": started_at,
        }
    )

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
            if tracker.asleep:
                continue
            try:
                displays = online_displays()
                snapshot = matching_snapshot(displays)
                current_monitors = matching_monitor_names(displays)
            except Exception as error:
                tracker.abandon_pending_check()
                log(f"display check skipped: {type(error).__name__}: {error}")
                continue
            plugged_in = tracker.evaluate(snapshot, now, fallback=fallback)
            if current_monitors != monitors:
                monitors = current_monitors
                update_state({"monitors": monitors, "monitors_at": time.time()})
            if plugged_in:
                handle_plug_in(settings)
    finally:
        Quartz.CGDisplayRemoveReconfigurationCallback(reconfigured, None)
        notifications.removeObserver_(observer)


def main(argv: list[str]) -> int:
    if argv == ["--list"]:
        list_displays()
        return 0
    if argv == ["--dim-now"]:
        result = run_manual(tool_settings.load(TOOL))
        return 1 if result.outcome is DimOutcome.FAILED else 0
    if argv in (["--status"], ["--status", "--json"]):
        print_status(automation_status(), as_json=argv[-1] == "--json")
        return 0
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    tool_settings.load(TOOL)  # fail at startup, not mid-run, on bad saved settings
    watch()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
