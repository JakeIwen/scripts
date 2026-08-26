"""Read-only telemetry summary and guarded voltage-check controllers.

This module supports both repository package imports and the flat sibling
layout used by the deployed van-dashboard service.
"""

__all__ = [
    "TelemetrySummaryReader",
    "VoltageCheckManager",
    "read_voltage_mon_sample",
    "valid_voltage",
]

if __package__:
    from .van_dashboard_common import (
        Request,
        TELEMETRY_SNAPSHOT_TIMEOUT,
        TELEMETRY_SNAPSHOT_URL,
        VOLTAGE_CHECK_TIMEOUT,
        VOLTAGE_MON_CSV,
        VOLTAGE_MON_TOOL,
        csv,
        datetime,
        json,
        math,
        os,
        run_command,
        subprocess,
        threading,
        time,
        urlopen,
    )
else:
    from van_dashboard_common import (
        Request,
        TELEMETRY_SNAPSHOT_TIMEOUT,
        TELEMETRY_SNAPSHOT_URL,
        VOLTAGE_CHECK_TIMEOUT,
        VOLTAGE_MON_CSV,
        VOLTAGE_MON_TOOL,
        csv,
        datetime,
        json,
        math,
        os,
        run_command,
        subprocess,
        threading,
        time,
        urlopen,
    )


def valid_voltage(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and 0 <= value <= 32
    )


def read_voltage_mon_sample(voltage_csv):
    """Read the newest valid sample from voltage_mon's bounded CSV tail."""
    try:
        with open(voltage_csv, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            end = handle.tell()
            start = max(0, end - 65536)
            handle.seek(start)
            data = handle.read()
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if start and lines:
        lines = lines[1:]
    try:
        rows = list(csv.reader(lines))
    except csv.Error:
        return None
    for row in reversed(rows):
        if len(row) < 2:
            continue
        try:
            value = float(row[1])
        except (TypeError, ValueError):
            continue
        if not valid_voltage(value):
            continue
        try:
            observed = datetime.datetime.fromisoformat(row[0])
            if observed.tzinfo is None:
                observed = observed.astimezone()
        except (TypeError, ValueError):
            continue
        return {
            "available": True,
            "value": round(value, 3),
            "unit": "V",
            "source": "voltage_mon",
            "observed_at": observed.isoformat(),
            "detail": "Last voltage_mon reading",
        }
    return None


class TelemetrySummaryReader:
    def __init__(
        self,
        snapshot_url=TELEMETRY_SNAPSHOT_URL,
        voltage_csv=VOLTAGE_MON_CSV,
        timeout=TELEMETRY_SNAPSHOT_TIMEOUT,
        opener=urlopen,
    ):
        self.snapshot_url = snapshot_url
        self.voltage_csv = voltage_csv
        self.timeout = timeout
        self.opener = opener

    @staticmethod
    def _valid_voltage(value):
        return valid_voltage(value)

    def _live_sample(self):
        request = Request(
            self.snapshot_url,
            headers={"Accept": "application/json"},
        )
        with self.opener(request, timeout=self.timeout) as response:
            payload = json.load(response)
        metric = payload.get("metrics", {}).get("battery.voltage", {})
        value = metric.get("value")
        if (
            not metric.get("available")
            or metric.get("stale")
            or not self._valid_voltage(value)
        ):
            return None
        return {
            "available": True,
            "value": round(float(value), 3),
            "unit": "V",
            "source": "live",
            "observed_at": metric.get("observed_at"),
            "detail": "Live telemetry",
        }

    def _voltage_mon_sample(self):
        return read_voltage_mon_sample(self.voltage_csv)

    def snapshot(self):
        try:
            live = self._live_sample()
        except (
            AttributeError,
            OSError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ):
            live = None
        sample = live or self._voltage_mon_sample()
        if sample:
            return sample
        return {
            "available": False,
            "value": None,
            "unit": "V",
            "source": None,
            "observed_at": None,
            "detail": "No battery voltage reading available",
        }


class VoltageCheckManager:
    """Run the fixed guarded voltage monitor without blocking HTTP requests."""

    def __init__(
        self,
        tool=VOLTAGE_MON_TOOL,
        voltage_csv=VOLTAGE_MON_CSV,
        command=run_command,
        timeout=VOLTAGE_CHECK_TIMEOUT,
        wall_clock=time.time,
    ):
        self.tool = tool
        self.voltage_csv = voltage_csv
        self.command = command
        self.timeout = timeout
        self.wall_clock = wall_clock
        self.lock = threading.Lock()
        self.thread = None
        self.start_sample = None
        self.operation = {
            "status": "idle",
            "started_at": None,
            "completed_at": None,
            "error": None,
        }

    def _sample_marker(self):
        sample = read_voltage_mon_sample(self.voltage_csv)
        if not sample:
            return None
        return sample["observed_at"], sample["value"]

    def snapshot(self):
        with self.lock:
            return dict(self.operation)

    def start(self):
        with self.lock:
            if self.operation["status"] == "running":
                return False
            self.start_sample = self._sample_marker()
            self.operation = {
                "status": "running",
                "started_at": int(self.wall_clock()),
                "completed_at": None,
                "error": None,
            }
            self.thread = threading.Thread(
                target=self._run,
                name="voltage-check",
                daemon=True,
            )
            self.thread.start()
            return True

    def _run(self):
        error = None
        try:
            result = self.command(
                [self.tool, "--no-notify"],
                timeout=self.timeout,
            )
            # voltage_mon uses 2 to report a successful measurement below its
            # warning threshold. It is still a valid manual voltage result.
            if result.returncode not in (0, 2):
                detail = (
                    result.stderr or result.stdout or "voltage check failed"
                ).strip()
                error = detail[-500:]
            elif self._sample_marker() == self.start_sample:
                error = "voltage monitor did not record a new voltage sample"
        except subprocess.TimeoutExpired:
            error = f"voltage check timed out after {self.timeout:g} seconds"
        except OSError as exc:
            error = f"could not start voltage check: {exc}"
        except Exception as exc:
            error = f"voltage check failed: {exc}"

        with self.lock:
            self.operation["status"] = "error" if error else "complete"
            self.operation["completed_at"] = int(self.wall_clock())
            self.operation["error"] = error
