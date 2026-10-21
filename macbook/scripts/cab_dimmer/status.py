"""Read-only automation status for the display dimmer."""
import json
import os
import time

import tool_automation
import tool_settings

from .model import DimOutcome, RunTrigger
from .state_store import TOOL, read_state

STATUS_OK, STATUS_IDLE, STATUS_UNKNOWN, STATUS_WARN, _STATUS_ERROR = tool_automation.STATUS_LEVELS


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


def _epoch(value):
    return float(value) if type(value) in (int, float) and value >= 0 else None


def _count(amount, noun):
    return f"{amount} {noun}" if amount == 1 else f"{amount} {noun}s"


def _last_run_value(last_run):
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
        value = f"Failed: {str(last_run.get('detail') or 'unknown error')[:450]} {suffix}"
    return value, _epoch(last_run.get("at")), STATUS_WARN if outcome is DimOutcome.FAILED else None


def automation_status(settings=None, *, state_dir=None):
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
        level, summary = STATUS_IDLE, "Disabled; plug-ins are recorded but the lights are left alone"
    else:
        level, summary = STATUS_OK, f"Watching {_count(len(monitors), 'monitor')}"
    last_value, last_at, last_level = _last_run_value(state.get("last_run"))
    plug_in = _epoch(state.get("last_plug_in"))
    fields = [
        tool_automation.status_field("Enabled", "Yes" if values["enabled"] else "No"),
        tool_automation.status_field("Brightness", f"{int(values['brightness'])}%"),
        tool_automation.status_field("Monitors", ", ".join(monitors) if monitors else "None", _epoch(state.get("monitors_at"))),
        tool_automation.status_field("Last plug-in", time=plug_in) if plug_in is not None else tool_automation.status_field("Last plug-in", "Never"),
        tool_automation.status_field("Last run", last_value, last_at, last_level),
        tool_automation.status_field("Agent", f"Running, pid {pid}" if running else "Not running", _epoch(agent.get("started_at"))),
    ]
    return tool_automation.status_record(level, summary, fields)


def print_status(record, *, as_json):
    if as_json:
        print(json.dumps(record, separators=(",", ":"), allow_nan=False))
        return
    print(f"{record['summary']} [{record['level']}]")
    for field in record["fields"]:
        value = field["value"] or ""
        if field["time"] is not None:
            rendered = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(field["time"]))
            value = f"{value} ({rendered})" if value else rendered
        suffix = f" [{field['level']}]" if field["level"] is not None else ""
        print(f"{field['label']}: {value}{suffix}")
