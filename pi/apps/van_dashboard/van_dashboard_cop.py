"""COP intent, exterior-alert, and read-only CAN-wake status controllers.

This module supports both repository package imports and the flat sibling
layout used by the deployed van-dashboard service.
"""

__all__ = [
    "CopAlertManager",
    "CopCanWakeStatusReader",
    "CopLedManager",
    "ignition_is_on",
]

if __package__:
    from .van_dashboard_common import (
        ACTIVE_MARKER,
        COP_CAN_WAKE_SERVICE,
        COP_CAN_WAKE_STATUS,
        COP_CAN_WAKE_STATUS_MAX_BYTES,
        COP_LED_BRIGHTNESS,
        COP_LED_COLOR_TEMP_KELVIN,
        COP_LED_CONNECT_GRACE,
        COP_LED_RETRY_INTERVAL,
        COP_LED_TARGET,
        COP_LED_VERIFY_INTERVAL,
        FLOOD_CHECK_INTERVAL,
        IGNITION_MARKER,
        NTFY_INTERVAL,
        NTFY_SEND,
        NTFY_TIMEOUT,
        RUNTIME_DIR,
        SYSTEMCTL,
        TUYA_LIGHT,
        TUYA_STATUS,
        TUYA_TOGGLE,
        copy,
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
        COP_LED_BRIGHTNESS,
        COP_LED_COLOR_TEMP_KELVIN,
        COP_LED_CONNECT_GRACE,
        COP_LED_RETRY_INTERVAL,
        COP_LED_TARGET,
        COP_LED_VERIFY_INTERVAL,
        FLOOD_CHECK_INTERVAL,
        IGNITION_MARKER,
        NTFY_INTERVAL,
        NTFY_SEND,
        NTFY_TIMEOUT,
        RUNTIME_DIR,
        SYSTEMCTL,
        TUYA_LIGHT,
        TUYA_STATUS,
        TUYA_TOGGLE,
        copy,
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
        self.ext_flood = "unknown"
        self.errors = {}
        self.last_ntfy = None
        self.ntfy_pending = False
        self.ntfy_thread = None
        self.next_flood_check = 0.0
        self.next_ntfy = 0.0

    def start(self):
        os.makedirs(self.runtime_dir, mode=0o750, exist_ok=True)
        if not self.thread:
            self.thread = threading.Thread(target=self._loop, name="cop-alert", daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()

    @property
    def active(self):
        return bool(self.store.get("cop_alert", False))

    def set_active(self, active):
        active = bool(active)
        was_active = self.active
        self.store.set("cop_alert", active)
        with self.lock:
            self.errors.clear()
            if active:
                self._touch(self.active_marker)
                self.next_flood_check = 0.0
                if not was_active:
                    self.next_ntfy = 0.0
            else:
                self._remove(self.active_marker)

        # Begin the activation notification before touching the Wi-Fi Tuya
        # device. The single-flight worker keeps a slow or disconnected ntfy
        # endpoint off both the request thread and the COP maintenance loop.
        if active and not was_active:
            self._queue_ntfy(self.clock())

        # Give the button immediate, deterministic switch behavior.  Background
        # retries and status reporting handle a temporarily unavailable HA API.
        desired = "on" if active and not self.ignition_on() else "off"
        ok, message = self._set_ext_flood(desired)
        if not ok:
            self._set_error("ext_flood", message)
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            last_error = "; ".join(self.errors.values()) or None
            return {
                "active": self.active,
                "ignition_on": self.ignition_on(),
                "ext_flood": self.ext_flood,
                "last_ntfy": self.last_ntfy,
                "last_error": last_error,
            }

    def tick(self):
        """Run one manager iteration; separated for focused tests."""
        now = self.clock()
        if not self.active:
            self._remove(self.active_marker)
            if now >= self.next_flood_check:
                self.next_flood_check = now + FLOOD_CHECK_INTERVAL
                self._get_ext_flood()
            return

        self._touch(self.active_marker)
        ignition_on = self.ignition_on()

        if now >= self.next_flood_check:
            self.next_flood_check = now + FLOOD_CHECK_INTERVAL
            desired = "off" if ignition_on else "on"
            actual = self._get_ext_flood()
            if actual != desired:
                ok, message = self._set_ext_flood(desired)
                if not ok:
                    self._set_error("ext_flood", message)

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

    def _set_ext_flood(self, desired):
        try:
            result = self.command([TUYA_TOGGLE, "ext_flood", desired], timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"ext_flood {desired} failed: {exc}"
        if result.returncode:
            detail = (result.stderr or result.stdout or "command failed").strip()
            return False, f"ext_flood {desired} failed: {detail}"
        with self.lock:
            self.ext_flood = desired
        self._set_error("ext_flood", None)
        return True, f"ext_flood is {desired}"

    def _get_ext_flood(self):
        try:
            result = self.command([TUYA_STATUS, "ext_flood"], timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._set_error("ext_flood", f"ext_flood status failed: {exc}")
            return "unknown"
        state = result.stdout.strip().lower()
        if result.returncode == 0 and state in ("on", "off"):
            with self.lock:
                self.ext_flood = state
            self._set_error("ext_flood", None)
            return state
        with self.lock:
            self.ext_flood = "unknown"
        self._set_error("ext_flood", "could not read ext_flood state")
        return "unknown"

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


class CopLedManager:
    """Apply and verify the COP ALERT exterior LED look off-thread."""

    def __init__(
        self,
        store,
        target=COP_LED_TARGET,
        command=run_command,
        ignition_on=ignition_is_on,
        clock=time.monotonic,
        wall_clock=time.time,
        retry_interval=COP_LED_RETRY_INTERVAL,
        verify_interval=COP_LED_VERIFY_INTERVAL,
        connect_grace=COP_LED_CONNECT_GRACE,
        brightness=COP_LED_BRIGHTNESS,
        color_temp_kelvin=COP_LED_COLOR_TEMP_KELVIN,
    ):
        self.store = store
        self.target = target
        self.command = command
        self.ignition_on = ignition_on
        self.clock = clock
        self.wall_clock = wall_clock
        self.retry_interval = retry_interval
        self.verify_interval = verify_interval
        self.connect_grace = connect_grace
        self.desired = {
            "brightness": brightness,
            "color_temp_kelvin": color_temp_kelvin,
        }
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = None
        self.was_active = False
        self.connect_started_at = None
        self.next_attempt = 0.0
        self.phase = "inactive"
        self.message = "COP ALERT is off"
        self.last_error = None
        self.last_attempt = None
        self.confirmed_at = None

    def start(self):
        if not self.thread:
            self.thread = threading.Thread(
                target=self._loop, name="cop-alert-ext-led", daemon=True
            )
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()

    def notify(self):
        with self.lock:
            self.next_attempt = 0.0
        self.wake_event.set()

    def snapshot(self):
        with self.lock:
            return {
                "phase": self.phase,
                "message": self.message,
                "last_error": self.last_error,
                "last_attempt": self.last_attempt,
                "confirmed_at": self.confirmed_at,
                "desired": copy.deepcopy(self.desired),
                "target": self.target,
            }

    def tick(self):
        now = self.clock()
        active = bool(self.store.get("cop_alert", False))
        with self.lock:
            if not active:
                self.was_active = False
                self.connect_started_at = None
                self.next_attempt = 0.0
                self.phase = "inactive"
                self.message = "COP ALERT is off"
                self.last_error = None
                self.confirmed_at = None
                return self.snapshot_unlocked()
            if not self.was_active:
                self.was_active = True
                self.connect_started_at = now
                self.next_attempt = 0.0
                self.phase = "preparing"
                self.message = "Preparing ext_led"
                self.last_error = None
                self.confirmed_at = None
            if now < self.next_attempt:
                return self.snapshot_unlocked()
            self.last_attempt = int(self.wall_clock())

        if self.ignition_on():
            with self.lock:
                self.connect_started_at = None
            self._schedule(
                "paused",
                "Ignition on · exterior alert is paused",
                None,
                now + self.retry_interval,
            )
            return self.snapshot()

        with self.lock:
            if self.connect_started_at is None:
                self.connect_started_at = now

        with self.lock:
            desired = copy.deepcopy(self.desired)

        target_status, target_error = self._read_light(self.target)
        if target_error or not target_status or target_status.get("state") == "unavailable":
            with self.lock:
                waiting_for = now - self.connect_started_at
            if waiting_for >= self.connect_grace:
                message = "ext_led unavailable · still retrying"
                error = target_error or (
                    f"{self.target} did not join Wi-Fi within {self.connect_grace:g} seconds"
                )
                phase = "unavailable"
            else:
                message = "Waiting for ext_led Wi-Fi"
                error = target_error
                phase = "waiting"
            self._schedule(phase, message, error, now + self.retry_interval)
            return self.snapshot()

        if self._matches(target_status, desired):
            self._confirmed(desired, now)
            return self.snapshot()

        with self.lock:
            self.phase = "applying"
            self.message = "Applying ext_led brightness and color"
            self.last_error = None
        set_error = self._set_light(self.target, desired)
        if set_error:
            self._schedule(
                "error", "Could not configure ext_led; retrying", set_error, now + self.retry_interval
            )
            return self.snapshot()

        confirmed, confirm_error = self._read_light(self.target)
        if confirmed and self._matches(confirmed, desired):
            self._confirmed(desired, now)
        elif confirm_error or not confirmed or confirmed.get("state") == "unavailable":
            self._schedule(
                "waiting",
                "ext_led accepted settings; waiting for Wi-Fi confirmation",
                confirm_error,
                now + self.retry_interval,
            )
        else:
            self._schedule(
                "verifying",
                "ext_led settings not confirmed yet; retrying",
                None,
                now + self.retry_interval,
            )
        return self.snapshot()

    def snapshot_unlocked(self):
        return {
            "phase": self.phase,
            "message": self.message,
            "last_error": self.last_error,
            "last_attempt": self.last_attempt,
            "confirmed_at": self.confirmed_at,
            "desired": copy.deepcopy(self.desired),
            "target": self.target,
        }

    def _read_light(self, entity):
        try:
            result = self.command([TUYA_LIGHT, "status", entity], timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"could not read {entity}: {exc}"
        if result.returncode:
            detail = (result.stderr or result.stdout or "status failed").strip()
            return None, f"could not read {entity}: {detail[-300:]}"
        try:
            status = json.loads(result.stdout)
        except (TypeError, ValueError):
            return None, f"could not read {entity}: invalid status response"
        return status if isinstance(status, dict) else None, None

    @staticmethod
    def _matches(status, desired):
        if not status or status.get("state") != "on":
            return False
        try:
            brightness = int(status.get("brightness"))
            kelvin = int(status.get("color_temp_kelvin"))
        except (TypeError, ValueError):
            return False
        return brightness == desired["brightness"] and abs(
            kelvin - desired["color_temp_kelvin"]
        ) <= 10

    def _set_light(self, entity, desired):
        try:
            result = self.command(
                [
                    TUYA_LIGHT,
                    "set",
                    entity,
                    str(desired["brightness"]),
                    str(desired["color_temp_kelvin"]),
                ],
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"could not set {entity}: {exc}"
        if result.returncode:
            detail = (result.stderr or result.stdout or "set failed").strip()
            return f"could not set {entity}: {detail[-300:]}"
        return None

    def _schedule(self, phase, message, error, next_attempt):
        with self.lock:
            self.phase = phase
            self.message = message
            self.last_error = error
            self.next_attempt = next_attempt

    def _confirmed(self, desired, now):
        percent = round(desired["brightness"] * 100 / 255)
        with self.lock:
            self.phase = "confirmed"
            self.message = f"Matched · {percent}% · {desired['color_temp_kelvin']} K"
            self.last_error = None
            self.confirmed_at = int(self.wall_clock())
            self.next_attempt = now + self.verify_interval

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                with self.lock:
                    self.phase = "error"
                    self.message = "ext_led manager failed; retrying"
                    self.last_error = str(exc)
                    self.next_attempt = self.clock() + self.retry_interval
            self.wake_event.wait(1.0)
            self.wake_event.clear()
