"""COP intent, exterior-alert, and read-only CAN-wake status controllers.

This module supports both repository package imports and the flat sibling
layout used by the deployed van-dashboard service.
"""

__all__ = [
    "CopAlertManager",
    "CopCanWakeStatusReader",
    "CopRelayManager",
    "ignition_is_on",
]

if __package__:
    from .van_dashboard_common import (
        ACTIVE_MARKER,
        COP_CAN_WAKE_SERVICE,
        COP_CAN_WAKE_STATUS,
        COP_CAN_WAKE_STATUS_MAX_BYTES,
        IGNITION_MARKER,
        NTFY_INTERVAL,
        NTFY_SEND,
        NTFY_TIMEOUT,
        RUNTIME_DIR,
        SYSTEMCTL,
        json,
        math,
        os,
        run_command,
        stat,
        subprocess,
        threading,
        time,
    )
else:
    from van_dashboard_common import (
        ACTIVE_MARKER,
        COP_CAN_WAKE_SERVICE,
        COP_CAN_WAKE_STATUS,
        COP_CAN_WAKE_STATUS_MAX_BYTES,
        IGNITION_MARKER,
        NTFY_INTERVAL,
        NTFY_SEND,
        NTFY_TIMEOUT,
        RUNTIME_DIR,
        SYSTEMCTL,
        json,
        math,
        os,
        run_command,
        stat,
        subprocess,
        threading,
        time,
    )


def ignition_is_on(path=IGNITION_MARKER):
    """Fail safe when the ignition marker cannot be inspected."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


class CopCanWakeStatusReader:
    """Read and sanitize the CAN-wake supervisor's untrusted status file."""

    STATES = {
        "idle",
        "starting",
        "arming_delay",
        "paused_ignition",
        "blocked",
        "waking",
        "active_waiting",
        "stopping",
        "stopped",
    }
    BOOLEAN_FIELDS = {
        "marker_active",
        "ignition_on",
        "transaction_in_progress",
    }
    NUMBER_FIELDS = {
        "activation_debounce_seconds",
        "preexisting_marker_delay_seconds",
        "safety_retry_seconds",
        "fixed_success_cadence_seconds",
    }
    STRING_FIELDS = {
        "next_attempt_at",
        "last_attempt_at",
        "last_success_at",
        "last_reason",
        "last_detail",
        "last_blocked_at",
        "last_blocked_reason",
        "last_blocked_detail",
        "generated_at",
    }

    def __init__(
        self,
        path=COP_CAN_WAKE_STATUS,
        command=run_command,
        systemctl=SYSTEMCTL,
        service=COP_CAN_WAKE_SERVICE,
        timeout=3,
        max_bytes=COP_CAN_WAKE_STATUS_MAX_BYTES,
    ):
        self.path = path
        self.command = command
        self.systemctl = systemctl
        self.service = service
        self.timeout = timeout
        self.max_bytes = max_bytes

    def snapshot(self):
        service_active, service_error = self._service_active()
        try:
            raw = self._read_regular_file()
            payload = json.loads(raw.decode("utf-8"))
            sanitized = self._sanitize(payload)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return {
                "available": False,
                "service_active": service_active,
                "error": service_error or self._safe_error(exc),
            }

        sanitized["service_active"] = service_active
        sanitized["available"] = service_active
        sanitized["error"] = service_error
        return sanitized

    def _service_active(self):
        try:
            result = self.command(
                [self.systemctl, "is-active", self.service],
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return False, "CAN wake supervisor status check timed out"
        except OSError:
            return False, "CAN wake supervisor status could not be checked"
        if result.returncode == 0 and result.stdout.strip() == "active":
            return True, None
        return False, "CAN wake supervisor is not active"

    def _read_regular_file(self):
        before = os.lstat(self.path)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("status path is not a regular file")
        if before.st_size > self.max_bytes:
            raise ValueError("status file is too large")

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags)
        try:
            opened = os.fstat(descriptor)
            after = os.lstat(self.path)
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError("opened status is not a regular file")
            if not stat.S_ISREG(after.st_mode):
                raise ValueError("status path changed to a non-regular file")
            identity = (before.st_dev, before.st_ino)
            if identity != (opened.st_dev, opened.st_ino) or identity != (
                after.st_dev,
                after.st_ino,
            ):
                raise ValueError("status file changed while opening")
            if opened.st_size > self.max_bytes:
                raise ValueError("status file is too large")
            chunks = []
            remaining = self.max_bytes + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            if len(content) > self.max_bytes:
                raise ValueError("status file is too large")
            return content
        finally:
            os.close(descriptor)

    def _sanitize(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("status JSON is not an object")
        if payload.get("service") != "van-cop-can-wake":
            raise ValueError("unexpected status service")
        if payload.get("role") != "c-can":
            raise ValueError("unexpected status role")
        schema_version = payload.get("schema_version")
        if isinstance(schema_version, bool) or schema_version != 1:
            raise ValueError("unsupported status schema")

        state = payload.get("state")
        if state not in self.STATES:
            raise ValueError("unknown supervisor state")
        sanitized = {"state": state}

        for field in self.BOOLEAN_FIELDS:
            if field not in payload:
                continue
            if not isinstance(payload[field], bool):
                raise ValueError(f"invalid {field}")
            sanitized[field] = payload[field]

        for field in self.NUMBER_FIELDS:
            if field not in payload:
                continue
            value = payload[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"invalid {field}")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid {field}")
            sanitized[field] = value

        if "wake_count" in payload:
            value = payload["wake_count"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("invalid wake_count")
            sanitized["wake_count"] = value

        for field in self.STRING_FIELDS:
            if field not in payload:
                continue
            value = payload[field]
            if value is not None and (not isinstance(value, str) or len(value) > 2048):
                raise ValueError(f"invalid {field}")
            sanitized[field] = value
        return sanitized

    @staticmethod
    def _safe_error(exc):
        if isinstance(exc, FileNotFoundError):
            return "CAN wake supervisor status is unavailable"
        return "CAN wake supervisor status is invalid"


class CopAlertManager:
    def __init__(
        self,
        store,
        command=run_command,
        ignition_on=ignition_is_on,
        runtime_dir=RUNTIME_DIR,
        clock=time.monotonic,
        wall_clock=time.time,
        light=None,
    ):
        self.store = store
        self.command = command
        self.ignition_on = ignition_on
        self.runtime_dir = runtime_dir
        self.active_marker = os.path.join(runtime_dir, os.path.basename(ACTIVE_MARKER))
        self.clock = clock
        self.wall_clock = wall_clock
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.light = light if light is not None else CopRelayManager(wall_clock=wall_clock)
        self.errors = {}
        self.last_ntfy = None
        self.ntfy_pending = False
        self.ntfy_thread = None
        self.next_ntfy = 0.0

    def start(self):
        os.makedirs(self.runtime_dir, mode=0o750, exist_ok=True)
        # Claim the line low before restoring persisted intent.
        self.light.update(False, False)
        if not self.thread:
            self.thread = threading.Thread(target=self._loop, name="cop-alert", daemon=True)
            self.thread.start()

    def stop(self):
        # Serialize against both request threads and the maintenance loop.
        with self.lock:
            self.stop_event.set()
            self.light.stop()

    @property
    def active(self):
        return bool(self.store.get("cop_alert", False))

    def set_active(self, active):
        with self.lock:
            if self.stop_event.is_set():
                raise RuntimeError("COP ALERT manager is stopping")
            active = bool(active)
            was_active = self.active
            self.store.set("cop_alert", active)
            self.errors.clear()
            if active:
                self._touch(self.active_marker)
                if not was_active:
                    self.next_ntfy = 0.0
            else:
                self._remove(self.active_marker)

            # GPIO commands are local and serialized. Never call Tuya lighting.
            self.light.update(active, self.ignition_on())
            if active and not was_active:
                self._queue_ntfy(self.clock())
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            light = self.light.snapshot()
            messages = list(self.errors.values())
            if light["last_error"]:
                messages.append(light["last_error"])
            return {
                "active": self.active,
                "ignition_on": self.ignition_on(),
                "relay_state": light["state"],
                "last_ntfy": self.last_ntfy,
                "last_error": "; ".join(messages) or None,
            }

    def tick(self):
        """Reconcile the dedicated relay every second, including while inactive."""
        with self.lock:
            if self.stop_event.is_set():
                return
            now = self.clock()
            active = self.active
            self.light.update(active, self.ignition_on())
            if not active:
                self._remove(self.active_marker)
                return
            self._touch(self.active_marker)
            if now >= self.next_ntfy:
                self._queue_ntfy(now)
            self._set_error("manager", None)

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                self._set_error("manager", f"COP ALERT loop error: {exc}")
            self.stop_event.wait(1.0)

    def _queue_ntfy(self, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            if self.ntfy_pending:
                return False
            self.ntfy_pending = True
            self.next_ntfy = now + NTFY_INTERVAL
            worker = threading.Thread(
                target=self._send_ntfy_worker,
                name="cop-alert-ntfy",
                daemon=True,
            )
            self.ntfy_thread = worker
        try:
            worker.start()
        except Exception as exc:
            with self.lock:
                self.ntfy_pending = False
                self.next_ntfy = now + min(30.0, NTFY_INTERVAL)
            self._set_error("ntfy", f"could not start COP ALERT ntfy worker: {exc}")
            return False
        return True

    def _send_ntfy_worker(self):
        try:
            ok, message = self._send_ntfy()
        except Exception as exc:
            ok, message = False, f"COP ALERT ntfy worker failed: {exc}"
        with self.lock:
            if ok:
                self.last_ntfy = int(self.wall_clock())
            elif self.active:
                retry_at = self.clock() + min(30.0, NTFY_INTERVAL)
                self.next_ntfy = min(self.next_ntfy, retry_at)
            still_active = self.active
            self.ntfy_pending = False
        self._set_error("ntfy", None if ok or not still_active else message)

    def _send_ntfy(self):
        try:
            result = self.command(
                [NTFY_SEND, "COP ALERT", "🥓 COP ALERT is active", "high", "bacon"],
                timeout=NTFY_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"COP ALERT ntfy failed: {exc}"
        if result.returncode:
            detail = (result.stderr or result.stdout or "command failed").strip()
            return False, f"COP ALERT ntfy failed: {detail}"
        return True, "COP ALERT ntfy sent"

    def _set_error(self, component, message):
        with self.lock:
            if message:
                self.errors[component] = message
            else:
                self.errors.pop(component, None)

    @staticmethod
    def _touch(path):
        os.makedirs(os.path.dirname(path), mode=0o750, exist_ok=True)
        with open(path, "a", encoding="utf-8"):
            os.utime(path, None)

    @staticmethod
    def _remove(path):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


class CopRelayManager:
    """Own GPIO17 for the active-high relay; readback verifies GPIO, not light.

    Uses the deployed Raspberry Pi OS libgpiod v1 Python API. Imports and line
    requests are lazy so module imports and read-only API snapshots have no
    hardware effects. One lock covers acquisition, writes, readback and stop.
    """

    def __init__(self, gpio_module=None, wall_clock=time.time):
        self.gpio_module = gpio_module
        self.wall_clock = wall_clock
        self.lock = threading.RLock()
        self.chip = None
        self.line = None
        self.stopped = False
        self.state = "unknown"
        self.phase = "inactive"
        self.message = "USB light relay not initialized"
        self.last_error = None
        self.last_attempt = None
        self.confirmed_at = None

    def _acquire(self):
        if self.line is not None:
            return
        gpio = self.gpio_module
        if gpio is None:
            import gpiod

            gpio = gpiod
        chip = gpio.Chip("gpiochip0")
        line = None
        requested = False
        try:
            if chip.label() != "pinctrl-bcm2711":
                raise RuntimeError("refusing GPIO controller other than Pi 4 BCM2711")
            line = chip.get_line(17)
            if line.name() != "GPIO17":
                raise RuntimeError("GPIO17 identity did not match")
            line.request(
                consumer="cop-alert-light",
                type=gpio.LINE_REQ_DIR_OUT,
                flags=gpio.LINE_REQ_FLAG_BIAS_PULL_DOWN,
                default_val=0,
            )
            requested = True
            if line.get_value() != 0:
                raise RuntimeError("GPIO17 initial low was not confirmed")
        except Exception:
            try:
                if requested:
                    try:
                        line.set_value(0)
                    finally:
                        line.release()
            finally:
                chip.close()
            raise
        self.chip = chip
        self.line = line

    def update(self, active, ignition_on):
        with self.lock:
            if self.stopped:
                return self.snapshot()
            self.last_attempt = int(self.wall_clock())
            desired = int(bool(active) and not ignition_on)
            try:
                self._acquire()
                if self.line.get_value() != desired:
                    self.line.set_value(desired)
                if self.line.get_value() != desired:
                    raise RuntimeError("GPIO17 readback did not match requested state")
                self.state = "on" if desired else "off"
                self.phase = (
                    "confirmed" if desired else ("paused" if active else "inactive")
                )
                self.message = (
                    "USB light relay on · GPIO17"
                    if desired else
                    ("Ignition on · USB light relay off" if active else "USB light relay off")
                )
                self.last_error = None
                self.confirmed_at = int(self.wall_clock())
            except Exception as exc:
                self.state = "unknown"
                self.phase = "error"
                self.message = "USB light relay unavailable"
                self.last_error = f"GPIO17 relay: {exc}"
                self.confirmed_at = None
                # If we own it, prefer low after an I/O/readback failure.
                # Keep ownership so the next tick can retry; never steal a line.
                if self.line is not None:
                    try:
                        self.line.set_value(0)
                    except Exception:
                        pass
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            return {
                "target": "GPIO17",
                "backend": "gpio-relay",
                "state": self.state,
                "phase": self.phase,
                "message": self.message,
                "last_error": self.last_error,
                "last_attempt": self.last_attempt,
                "confirmed_at": self.confirmed_at,
                "verification": "GPIO readback; lamp output is not measured",
            }

    def stop(self):
        with self.lock:
            self.stopped = True
            try:
                if self.line is not None:
                    self.line.set_value(0)
                    if self.line.get_value() != 0:
                        raise RuntimeError("GPIO17 shutdown low was not confirmed")
                    self.state = "off"
                    self.phase = "inactive"
                    self.message = "USB light relay off"
                    self.last_error = None
            finally:
                try:
                    if self.line is not None:
                        self.line.release()
                finally:
                    self.line = None
                    if self.chip is not None:
                        self.chip.close()
                        self.chip = None


if __name__ == "__main__":
    # systemd ExecStopPost: low after normal exit, crash, or forced termination.
    # Exclusive request fails if another consumer owns GPIO17; no pinctrl bypass.
    import sys

    if sys.argv[1:] != ["--relay-off"]:
        raise SystemExit("usage: van_dashboard_cop.py --relay-off")
    relay = CopRelayManager()
    result = relay.update(False, False)
    try:
        if result["last_error"]:
            raise SystemExit(result["last_error"])
    finally:
        relay.stop()
