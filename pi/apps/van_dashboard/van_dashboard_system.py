"""Ignition-monitor, service-restart, uptime, and system-power controllers.

This module supports both repository package imports and the flat sibling
layout used by the deployed van-dashboard service.
"""

__all__ = [
    "DashboardRestartController",
    "DashboardRestartError",
    "IgnitionMonitorCommandError",
    "IgnitionMonitorController",
    "SystemPowerController",
    "SystemPowerError",
    "read_system_uptime",
]

if __package__:
    from .van_dashboard_common import (
        DASHBOARD_RESTART_TIMEOUT,
        DASHBOARD_SERVICE,
        IGNITIONMONCTL,
        IGNITIONMON_MAX_MINUTES,
        IGNITIONMON_TIMEOUT,
        PROC_UPTIME,
        SAFE_POWER_DOWN,
        SAFE_REBOOT,
        SUDO,
        SYSTEMCTL,
        SYSTEMD_RUN,
        SYSTEM_POWER_TIMEOUT,
        copy,
        datetime,
        json,
        math,
        os,
        run_command,
        subprocess,
        threading,
        time,
    )
else:
    from van_dashboard_common import (
        DASHBOARD_RESTART_TIMEOUT,
        DASHBOARD_SERVICE,
        IGNITIONMONCTL,
        IGNITIONMON_MAX_MINUTES,
        IGNITIONMON_TIMEOUT,
        PROC_UPTIME,
        SAFE_POWER_DOWN,
        SAFE_REBOOT,
        SUDO,
        SYSTEMCTL,
        SYSTEMD_RUN,
        SYSTEM_POWER_TIMEOUT,
        copy,
        datetime,
        json,
        math,
        os,
        run_command,
        subprocess,
        threading,
        time,
    )


class IgnitionMonitorCommandError(RuntimeError):
    pass


class IgnitionMonitorController:
    """Control the durable override without stopping the monitoring service."""

    CONTROL_FIELDS = {
        "version",
        "status",
        "active",
        "deadline",
        "remaining_seconds",
        "checked_at",
    }

    def __init__(
        self,
        control=IGNITIONMONCTL,
        systemctl=SYSTEMCTL,
        command=run_command,
        timeout=IGNITIONMON_TIMEOUT,
    ):
        self.control = control
        self.systemctl = systemctl
        self.command = command
        self.timeout = timeout

    def _run(self, args, label):
        try:
            result = self.command(args, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise IgnitionMonitorCommandError(
                f"{label} timed out after {self.timeout:g} seconds"
            ) from exc
        except OSError as exc:
            raise IgnitionMonitorCommandError(f"could not run {label}: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or f"{label} failed").strip()[-500:]
            raise IgnitionMonitorCommandError(detail)
        return result.stdout

    @classmethod
    def parse_control_status(cls, output):
        try:
            payload = json.loads(output)
        except (TypeError, ValueError) as exc:
            raise IgnitionMonitorCommandError(
                "ignitionmonctl returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict) or set(payload) != cls.CONTROL_FIELDS:
            raise IgnitionMonitorCommandError("ignitionmonctl returned an invalid schema")
        if payload.get("version") != 1 or payload.get("status") not in ("active", "disabled"):
            raise IgnitionMonitorCommandError("ignitionmonctl returned an invalid status")
        if not isinstance(payload.get("active"), bool):
            raise IgnitionMonitorCommandError("ignitionmonctl returned an invalid active state")
        remaining = payload.get("remaining_seconds")
        checked_at = payload.get("checked_at")
        deadline = payload.get("deadline")
        if (
            not isinstance(remaining, int)
            or isinstance(remaining, bool)
            or remaining < 0
            or not isinstance(checked_at, int)
            or isinstance(checked_at, bool)
            or checked_at <= 0
        ):
            raise IgnitionMonitorCommandError("ignitionmonctl returned invalid timing data")
        if payload["status"] == "active":
            valid = payload["active"] is True and deadline is None and remaining == 0
        else:
            valid = (
                payload["active"] is False
                and isinstance(deadline, int)
                and not isinstance(deadline, bool)
                and deadline > checked_at
                and deadline - checked_at == remaining
            )
        if not valid:
            raise IgnitionMonitorCommandError("ignitionmonctl returned inconsistent state")
        return payload

    @staticmethod
    def parse_service_status(output):
        values = {}
        for line in str(output).splitlines():
            key, separator, value = line.partition("=")
            if separator and key in ("ActiveState", "SubState", "UnitFileState"):
                values[key] = value.strip()
        if set(values) != {"ActiveState", "SubState", "UnitFileState"} or any(
            not value for value in values.values()
        ):
            raise IgnitionMonitorCommandError("systemd returned an invalid service status")
        return {
            "active_state": values["ActiveState"],
            "sub_state": values["SubState"],
            "unit_file_state": values["UnitFileState"],
            "running": values["ActiveState"] == "active" and values["SubState"] == "running",
            "enabled": values["UnitFileState"] in ("enabled", "enabled-runtime"),
        }

    def status(self):
        service_output = self._run(
            [
                self.systemctl,
                "show",
                "ignitionmon.service",
                "--property=ActiveState",
                "--property=SubState",
                "--property=UnitFileState",
                "--no-pager",
            ],
            "ignitionmon service status",
        )
        control_output = self._run(
            [self.control, "status", "--json"], "ignition monitor status"
        )
        return {
            "service": self.parse_service_status(service_output),
            "monitor": self.parse_control_status(control_output),
        }

    def disable(self, minutes):
        if (
            not isinstance(minutes, int)
            or isinstance(minutes, bool)
            or not 1 <= minutes <= IGNITIONMON_MAX_MINUTES
        ):
            raise ValueError(
                f"duration must be from 1 to {IGNITIONMON_MAX_MINUTES} minutes"
            )
        self._run(
            [self.control, "disable", f"{minutes}m"], "ignition monitor disable"
        )
        return self.status()

    def enable(self):
        self._run([self.control, "enable"], "ignition monitor enable")
        return self.status()


class SystemPowerError(RuntimeError):
    pass


class DashboardRestartError(RuntimeError):
    pass


class DashboardRestartController:
    """Schedule a fixed dashboard-only restart after the HTTP response returns."""

    def __init__(
        self,
        sudo=SUDO,
        systemd_run=SYSTEMD_RUN,
        systemctl=SYSTEMCTL,
        service=DASHBOARD_SERVICE,
        command=run_command,
        timeout=DASHBOARD_RESTART_TIMEOUT,
        wall_clock=time.time,
    ):
        self.sudo = sudo
        self.systemd_run = systemd_run
        self.systemctl = systemctl
        self.service = service
        self.command = command
        self.timeout = timeout
        self.wall_clock = wall_clock

    def restart(self):
        scheduled_at = int(self.wall_clock())
        unit = f"van-dashboard-web-restart-{os.getpid()}-{scheduled_at}"
        args = [
            self.sudo,
            "-n",
            self.systemd_run,
            "--quiet",
            "--collect",
            f"--unit={unit}",
            "--on-active=1s",
            self.systemctl,
            "restart",
            self.service,
        ]
        try:
            result = self.command(args, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise DashboardRestartError(
                f"dashboard restart scheduling timed out after {self.timeout:g} seconds"
            ) from exc
        except OSError as exc:
            raise DashboardRestartError(
                f"could not schedule dashboard restart: {exc}"
            ) from exc
        if result.returncode:
            detail = (
                result.stderr or result.stdout or "systemd rejected the restart"
            ).strip()[-500:]
            raise DashboardRestartError(detail)
        return {"scheduled_at": scheduled_at}


def read_system_uptime(path=PROC_UPTIME, wall_clock=time.time):
    try:
        with open(path, encoding="utf-8") as handle:
            seconds = float(handle.read().split()[0])
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("invalid uptime")
    except (OSError, IndexError, ValueError):
        return {"seconds": None, "booted_at": None}
    now = float(wall_clock())
    return {
        "seconds": int(seconds),
        "booted_at": datetime.datetime.fromtimestamp(
            now - seconds, datetime.timezone.utc
        ).isoformat(),
    }


class SystemPowerController:
    def __init__(
        self,
        scripts=None,
        command=run_command,
        timeout=SYSTEM_POWER_TIMEOUT,
        wall_clock=time.time,
    ):
        self.scripts = dict(
            scripts
            or {
                "reboot": SAFE_REBOOT,
                "power-down": SAFE_POWER_DOWN,
            }
        )
        self.command = command
        self.timeout = timeout
        self.wall_clock = wall_clock
        self.lock = threading.Lock()
        self.thread = None
        self.operation = {
            "status": "idle",
            "action": None,
            "started_at": None,
            "completed_at": None,
            "error": None,
        }

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.operation)

    def start_action(self, action):
        if action not in ("reboot", "power-down") or action not in self.scripts:
            raise ValueError("unknown system power action")
        with self.lock:
            if self.operation["status"] == "running":
                raise SystemPowerError("another system power action is already running")
            started_at = int(self.wall_clock())
            self.operation = {
                "status": "running",
                "action": action,
                "started_at": started_at,
                "completed_at": None,
                "error": None,
            }
            self.thread = threading.Thread(
                target=self._run_action,
                args=(action, started_at),
                name="system-power-action",
                daemon=True,
            )
            self.thread.start()
        return self.snapshot()

    def _run_action(self, action, started_at):
        error = None
        try:
            result = self.command([self.scripts[action]], timeout=self.timeout)
            if result.returncode:
                error = (
                    result.stderr
                    or result.stdout
                    or f"{action} preparation failed"
                ).strip()[-500:]
        except subprocess.TimeoutExpired:
            error = f"{action} preparation timed out after {self.timeout:g} seconds"
        except OSError as exc:
            error = f"could not start {action}: {exc}"
        with self.lock:
            if self.operation.get("started_at") == started_at:
                self.operation.update(
                    {
                        "status": "error" if error else "complete",
                        "completed_at": int(self.wall_clock()),
                        "error": error,
                    }
                )
