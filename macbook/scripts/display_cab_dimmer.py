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

from pathlib import Path
import sys
import time

import tool_settings
from cab_dimmer.displays import online_displays
from cab_dimmer.dashboard import (
    DASHBOARD_URL, DASHBOARD_URL_ENV, HTTP_TIMEOUT_SECONDS, DashboardError,
    dim_cab_lights as _dim_cab_lights,
)
from cab_dimmer.model import (
    DimOutcome, DimResult, DisplayInfo, MIRROR_NAME, MONITOR_NAME, PlugTracker, RunTrigger,
    display_name_matches, matching_monitor_names, matching_snapshot,
)
from cab_dimmer.state_store import STATE_DIR_ENV, read_state, update_state
from cab_dimmer.runtime import watch as watch_runtime
from cab_dimmer.status import automation_status, print_status

TOOL = "display-cab-dimmer"


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


def dim_cab_lights(
    brightness: int,
    *,
    dashboard_url: str | None = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
) -> DimResult:
    return _dim_cab_lights(brightness, dashboard_url=dashboard_url,
                           timeout=timeout, logger=log)


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


def watch() -> None:
    watch_runtime(online_displays=online_displays,
                  matching_snapshot=matching_snapshot,
                  matching_monitor_names=matching_monitor_names,
                  update_state=update_state, handle_plug_in=handle_plug_in,
                  logger=log)


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
