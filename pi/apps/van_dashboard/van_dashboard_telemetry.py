"""Read-only telemetry summary and guarded voltage-check controllers.

This module supports both repository package imports and the flat sibling
layout used by the deployed van-dashboard service.
"""

__all__ = [
    "TelemetrySummaryReader",
    "VoltageCheckManager",
    "newest_voltage_sample",
    "read_engine_off_voltage_sample",
    "read_voltage_mon_sample",
    "valid_voltage",
]

if __package__:
    from .van_dashboard_common import (
        Request,
        ENGINE_OFF_VOLTAGE_STATUS,
        ENGINE_OFF_VOLTAGE_STATUS_MAX_BYTES,
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
        stat,
        subprocess,
        threading,
        time,
        urlopen,
    )
else:
    from van_dashboard_common import (
        Request,
        ENGINE_OFF_VOLTAGE_STATUS,
        ENGINE_OFF_VOLTAGE_STATUS_MAX_BYTES,
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
        stat,
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


def _aware_datetime(value):
    try:
        parsed = datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(datetime.timezone.utc)


def read_engine_off_voltage_sample(
    status_path,
    maximum_bytes=ENGINE_OFF_VOLTAGE_STATUS_MAX_BYTES,
):
    """Read the broker's bounded, regular-file passive engine-off sample."""

    try:
        metadata = os.lstat(status_path)
    except OSError:
        return None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or metadata.st_size > maximum_bytes
    ):
        return None
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(status_path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size <= 0
            or opened.st_size > maximum_bytes
        ):
            os.close(descriptor)
            return None
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            text = handle.read(maximum_bytes + 1)
        if len(text.encode("utf-8")) > maximum_bytes:
            return None
        payload = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or payload.get("service") != "van-telemetry"
        or payload.get("role") != "engine_off_voltage"
        or payload.get("sample_type") != "engine_off"
        or payload.get("unit") != "V"
        or payload.get("quality") != "verified"
        or payload.get("acquisition") not in ("passive", "passive_broadcast")
        or not valid_voltage(payload.get("value"))
    ):
        return None
    bus = payload.get("bus")
    source = payload.get("source")
    if (bus, source) not in (
        ("c-can", "ccan.broadcast.0x41a"),
        ("b-can", "bcan.broadcast.0x46c"),
    ):
        return None
    observed = _aware_datetime(payload.get("observed_at"))
    stopped = _aware_datetime(payload.get("engine_stopped_at"))
    saved = _aware_datetime(payload.get("saved_at"))
    if (
        observed is None
        or stopped is None
        or saved is None
        or observed < stopped
        or saved < observed
    ):
        return None
    return {
        "available": True,
        "value": round(float(payload["value"]), 3),
        "unit": "V",
        "source": "engine_off",
        "observed_at": observed.isoformat(),
        "detail": "Engine-off passive sample",
    }


def newest_voltage_sample(*samples):
    """Return the valid sample with the newest aware observation timestamp."""

    dated = []
    for sample in samples:
        if not sample or not sample.get("available"):
            continue
        observed = _aware_datetime(sample.get("observed_at"))
        if observed is not None:
            dated.append((observed, sample))
    return max(dated, key=lambda item: item[0])[1] if dated else None


class TelemetrySummaryReader:
    def __init__(
        self,
        snapshot_url=TELEMETRY_SNAPSHOT_URL,
        voltage_csv=VOLTAGE_MON_CSV,
        engine_off_status=ENGINE_OFF_VOLTAGE_STATUS,
        timeout=TELEMETRY_SNAPSHOT_TIMEOUT,
        opener=urlopen,
    ):
        self.snapshot_url = snapshot_url
        self.voltage_csv = voltage_csv
        self.engine_off_status = engine_off_status
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

    def _engine_off_sample(self):
        return read_engine_off_voltage_sample(self.engine_off_status)

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
        sample = live or newest_voltage_sample(
            self._engine_off_sample(),
            self._voltage_mon_sample(),
        )
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
