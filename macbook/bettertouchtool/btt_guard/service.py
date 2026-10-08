"""Safe launchd lifecycle for the finite, periodic BTT Guard audit job.

Unlike the repository's persistent web services, this job intentionally has no
KeepAlive process, HTTP health endpoint, or stable PID.  launchd runs one audit
at a time and ``monitor-health.json`` records successful job completion.
"""
from __future__ import annotations

import grp
import math
import os
from pathlib import Path
import plistlib
import pwd
import re
import stat
import subprocess
import sys
import time
import uuid
from typing import Any

from .model import AuditStatus, MonitorHealth, SCHEMA_VERSION, ServiceVerb
from .storage import load_json, private_dir, write_bytes


LABEL = "com.jacobr.btt-guard"
TARGET = f"system/{LABEL}"
INSTALLED = Path("/Library/LaunchDaemons") / f"{LABEL}.plist"
PYTHON = "/usr/bin/python3"
INTERVAL_SECONDS = 60
HEARTBEAT_FRESHNESS_SECONDS = 3 * INTERVAL_SECONDS
VERIFY_TIMEOUT_SECONDS = 30.0
POLL_SECONDS = 0.25
HEARTBEAT_NAME = "monitor-health.json"
LOG_NAMES = ("launchd.stdout.log", "launchd.stderr.log")
HEARTBEAT_FIELDS = set(MonitorHealth.__annotations__)


class ServiceError(RuntimeError):
    """The requested service operation failed closed."""


def _owner() -> tuple[int, str]:
    uid = os.getuid()
    if uid == 0:
        raise ServiceError(
            "Run the BTT Guard controller as the normal owner; do not sudo Python."
        )
    username = pwd.getpwuid(uid).pw_name
    if not username or username == "root":
        raise ServiceError("BTT Guard requires a non-root service owner.")
    return uid, username


def _paths(state_dir: Path, root: Path) -> tuple[Path, Path]:
    root = Path(root).expanduser().resolve()
    state_dir = Path(os.path.abspath(os.path.expanduser(str(state_dir))))
    expected = root / ".local" / "btt-guard"
    if state_dir != expected:
        raise ServiceError(f"State directory must be {expected}.")
    if not root.is_dir():
        raise ServiceError("BTT Guard workspace root is not a directory.")
    return state_dir, root


def service_configuration(state_dir: Path, root: Path) -> dict[str, Any]:
    """Return the complete one-shot launchd configuration."""
    uid, username = _owner()
    del uid
    state_dir, root = _paths(state_dir, root)
    return {
        "Label": LABEL,
        "UserName": username,
        "ProgramArguments": [
            PYTHON,
            "-B",
            "-m",
            "macbook.bettertouchtool.btt_guard",
            "--state-dir",
            str(state_dir),
            "monitor",
            "--managed",
        ],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                                 "HOME": pwd.getpwuid(os.getuid()).pw_dir,
                                 "USER": username, "LOGNAME": username},
        "RunAtLoad": True,
        "StartInterval": INTERVAL_SECONDS,
        "ThrottleInterval": 10,
        "ExitTimeOut": 20,
        "ProcessType": "Background",
        "Umask": 0o077,
        "StandardOutPath": str(state_dir / LOG_NAMES[0]),
        "StandardErrorPath": str(state_dir / LOG_NAMES[1]),
    }


def _run(arguments: list[str], *, privileged: bool = False) -> str:
    command = arguments
    if privileged:
        sudo_flags = [] if sys.stdin.isatty() else ["-n"]
        command = ["/usr/bin/sudo", *sudo_flags, *arguments]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
        raise ServiceError(f"{Path(arguments[0]).name} failed: {detail}")
    return result.stdout


def _touch_private_log(path: Path) -> None:
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ServiceError(f"Cannot open private service log: {path}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ServiceError(f"Service log belongs to another owner: {path}")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _prepare(state_dir: Path, root: Path) -> dict[str, Any]:
    state_dir, root = _paths(state_dir, root)
    configuration = service_configuration(state_dir, root)
    payload = state_dir / f"{LABEL}.plist"
    content = plistlib.dumps(configuration, fmt=plistlib.FMT_XML, sort_keys=False)
    try:
        private_dir(state_dir)
        write_bytes(payload, content)
    except (OSError, ValueError) as exc:
        raise ServiceError(str(exc)) from exc
    _run(["/usr/bin/plutil", "-lint", str(payload)])
    for name in LOG_NAMES:
        _touch_private_log(state_dir / name)
    return {
        "action": "prepare",
        "prepared": True,
        "installed": False,
        "payload": str(payload),
        "service": TARGET,
    }


def _wheel_gid() -> int:
    try:
        return grp.getgrnam("wheel").gr_gid
    except KeyError:
        return 0


def _read_installed(state_dir: Path, root: Path) -> dict[str, Any] | None:
    if not INSTALLED.exists() and not INSTALLED.is_symlink():
        return None
    info = INSTALLED.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        raise ServiceError("Refusing a symlinked installed service plist.")
    if not stat.S_ISREG(info.st_mode):
        raise ServiceError("Installed service plist is not a regular file.")
    if info.st_uid != 0 or info.st_gid != _wheel_gid() or mode != 0o644:
        raise ServiceError("Installed service plist must be root:wheel mode 0644.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(INSTALLED, flags)
        with os.fdopen(descriptor, "rb") as source:
            configuration = plistlib.load(source)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ServiceError("Cannot read the installed service plist.") from exc
    expected = service_configuration(state_dir, root)
    if configuration != expected:
        raise ServiceError(
            "Refusing to control a service belonging to another workspace or user."
        )
    return configuration


def _job() -> dict[str, Any]:
    result = subprocess.run(
        ["/bin/launchctl", "print", TARGET],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr or result.stdout
        if re.search(r"could not find service|service .* not found", detail, re.I):
            return {"loaded": False, "state": None}
        raise ServiceError(f"Cannot inspect {TARGET}: {detail.strip()}")
    match = re.search(r"^\s*state = (.+?)\s*$", result.stdout, re.M)
    return {"loaded": True, "state": match.group(1) if match else None}


def _enabled() -> bool:
    text = _run(["/bin/launchctl", "print-disabled", "system"])
    line = next((line for line in text.splitlines() if f'"{LABEL}"' in line), None)
    return line is None or re.search(r"=>\s*(?:true|disabled)\s*$", line) is None


def _validate_heartbeat(value: object, root: Path) -> dict[str, Any]:
    uid, _ = _owner()
    if not isinstance(value, dict) or set(value) != HEARTBEAT_FIELDS:
        raise ServiceError("Monitor heartbeat has unexpected or missing fields.")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ServiceError("Monitor heartbeat has an unsupported schema.")
    try:
        parsed_run_id = uuid.UUID(value.get("run_id", ""))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ServiceError("Monitor heartbeat has an invalid run ID.") from exc
    if str(parsed_run_id) != value["run_id"]:
        raise ServiceError("Monitor heartbeat run ID is not canonical.")
    pid = value.get("pid")
    completed_at = value.get("completed_at")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ServiceError("Monitor heartbeat has an invalid PID.")
    if value.get("uid") != uid or value.get("root") != str(root):
        raise ServiceError("Monitor heartbeat belongs to another workspace or user.")
    if (
        not isinstance(completed_at, (int, float))
        or isinstance(completed_at, bool)
        or not math.isfinite(completed_at)
        or completed_at <= 0
    ):
        raise ServiceError("Monitor heartbeat has an invalid completion time.")
    try:
        AuditStatus(value.get("audit_status"))
    except (TypeError, ValueError) as exc:
        raise ServiceError("Monitor heartbeat has an invalid audit status.") from exc
    return dict(value)


def _heartbeat(state_dir: Path, root: Path, *, now: float | None = None) -> dict[str, Any]:
    path = state_dir / HEARTBEAT_NAME
    try:
        value = load_json(path)
        record = _validate_heartbeat(value, root)
    except FileNotFoundError:
        return {"state": "missing", "fresh": False, "audit_status": None}
    except (OSError, ValueError, ServiceError):
        return {"state": "invalid", "fresh": False, "audit_status": None}
    current_time = time.time() if now is None else now
    age = current_time - record["completed_at"]
    fresh = 0 <= age <= HEARTBEAT_FRESHNESS_SECONDS
    return {
        "state": "fresh" if fresh else "stale",
        "fresh": fresh,
        "age_seconds": age,
        "run_id": record["run_id"],
        "pid": record["pid"],
        "completed_at": record["completed_at"],
        "audit_status": record["audit_status"],
    }


def _status(state_dir: Path, root: Path) -> dict[str, Any]:
    installed = _read_installed(state_dir, root)
    job = _job()
    enabled = _enabled()
    health = _heartbeat(state_dir, root)
    supervised = bool(installed and enabled and job["loaded"] and health["fresh"])
    return {
        "service": TARGET,
        "installed": installed is not None,
        "configuration_ok": installed is not None,
        "ownership_ok": installed is not None,
        "startup_enabled": enabled,
        "job": job,
        "heartbeat": health,
        "audit_status": health["audit_status"],
        "supervised": supervised,
        "interval_seconds": INTERVAL_SECONDS,
        "logs": str(state_dir),
    }


def _require_installed(state_dir: Path, root: Path) -> dict[str, Any]:
    configuration = _read_installed(state_dir, root)
    if configuration is None:
        raise ServiceError("BTT Guard service is not installed.")
    return configuration


def _install(state_dir: Path, root: Path) -> dict[str, Any]:
    prepared = _prepare(state_dir, root)
    existing = _read_installed(state_dir, root)
    was_loaded = _job()["loaded"]
    if was_loaded and existing is None:
        raise ServiceError(
            "Refusing to replace a loaded service without its matching installed plist."
        )
    _run(
        [
            "/usr/bin/install",
            "-o",
            "root",
            "-g",
            "wheel",
            "-m",
            "644",
            prepared["payload"],
            str(INSTALLED),
        ],
        privileged=True,
    )
    _require_installed(state_dir, root)
    _run(["/bin/launchctl", "enable", TARGET], privileged=True)
    if was_loaded:
        _run(["/bin/launchctl", "bootout", TARGET], privileged=True)
    _run(["/bin/launchctl", "bootstrap", "system", str(INSTALLED)], privileged=True)
    return {"action": "install", "installed": True, "service": TARGET}


def _start(state_dir: Path, root: Path) -> dict[str, Any]:
    _require_installed(state_dir, root)
    job = _job()
    _run(["/bin/launchctl", "enable", TARGET], privileged=True)
    if job["loaded"]:
        _run(["/bin/launchctl", "kickstart", TARGET], privileged=True)
    else:
        _run(["/bin/launchctl", "bootstrap", "system", str(INSTALLED)], privileged=True)
    return {"action": "start", "started": True, "service": TARGET}


def _stop(state_dir: Path, root: Path) -> dict[str, Any]:
    _require_installed(state_dir, root)
    job = _job()
    _run(["/bin/launchctl", "disable", TARGET], privileged=True)
    if job["loaded"]:
        _run(["/bin/launchctl", "bootout", TARGET], privileged=True)
    return {"action": "stop", "stopped": True, "service": TARGET}


def _uninstall(state_dir: Path, root: Path) -> dict[str, Any]:
    _require_installed(state_dir, root)
    job = _job()
    _run(["/bin/launchctl", "disable", TARGET], privileged=True)
    if job["loaded"]:
        _run(["/bin/launchctl", "bootout", TARGET], privileged=True)
    _run(["/bin/rm", "--", str(INSTALLED)], privileged=True)
    return {
        "action": "uninstall",
        "registration_removed": True,
        "state_retained": str(state_dir),
        "service": TARGET,
    }


def _is_new_heartbeat(
    before: dict[str, Any], after: dict[str, Any], started_at: float
) -> bool:
    if not after.get("fresh"):
        return False
    before_id = before.get("run_id")
    if before_id is not None:
        return after.get("run_id") != before_id
    return after.get("completed_at", 0) >= started_at


def _verify(state_dir: Path, root: Path) -> dict[str, Any]:
    configuration = _require_installed(state_dir, root)
    job = _job()
    if not job["loaded"] or not _enabled():
        raise ServiceError("Install, enable, and load BTT Guard before verification.")
    if configuration.get("RunAtLoad") is not True or configuration.get(
        "StartInterval"
    ) != INTERVAL_SECONDS:
        raise ServiceError("Installed BTT Guard schedule does not match the expected job.")
    before = _heartbeat(state_dir, root)
    started_at = time.time()
    deadline = time.monotonic() + VERIFY_TIMEOUT_SECONDS
    _run(["/bin/launchctl", "kickstart", TARGET], privileged=True)
    while time.monotonic() < deadline:
        after = _heartbeat(state_dir, root)
        if _is_new_heartbeat(before, after, started_at):
            return {
                "action": "verify",
                "verified": True,
                "service": TARGET,
                "run_id": after["run_id"],
                "audit_status": after["audit_status"],
            }
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(POLL_SECONDS, remaining))
    raise ServiceError(
        f"BTT Guard did not write a new heartbeat within {VERIFY_TIMEOUT_SECONDS:g} seconds."
    )


def run_service(verb: str, state_dir: Path, root: Path) -> dict[str, Any]:
    """Run one service-controller verb and return a JSON-serializable result."""
    state_dir, root = _paths(state_dir, root)
    _owner()
    actions = {
        ServiceVerb.PREPARE: _prepare,
        ServiceVerb.INSTALL: _install,
        ServiceVerb.STATUS: _status,
        ServiceVerb.START: _start,
        ServiceVerb.STOP: _stop,
        ServiceVerb.VERIFY: _verify,
        ServiceVerb.UNINSTALL: _uninstall,
    }
    action = actions.get(verb)
    if action is None:
        raise ServiceError(
            "Service action must be prepare, install, status, start, stop, verify, or uninstall."
        )
    return action(state_dir, root)
