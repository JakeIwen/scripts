#!/usr/bin/env python3
"""Phone-friendly control surface for vanpi.

The first dashboard feature is COP ALERT.  While active it:

* keeps Home Assistant's ``switch.ext_flood`` on while the engine is stopped;
* periodically wakes C-CAN with a benign RF Hub identification read so the
  dash accessory rail (and therefore the dashcam) stays awake;
* emits a bacon ntfy notification immediately and every five minutes; and
* publishes fresh, passive C-CAN engine-running evidence for ignition_on.sh.

The CAN receive path is passive.  The only transmitted frame is the explicitly
requested RF Hub ReadDataByIdentifier wake.  This process deliberately does not
bring can0 up/down or change its bitrate/listen-only state because the interface
is shared with other vehicle tooling.
"""

import copy
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from flask import Flask, jsonify, render_template, request


PORT = int(os.environ.get("VAN_DASHBOARD_PORT", "8788"))
STATE_PATH = os.path.expanduser(
    os.environ.get("VAN_DASHBOARD_STATE_PATH", "~/.van_dashboard_state.json")
)
RUNTIME_DIR = os.environ.get("VAN_DASHBOARD_RUNTIME_DIR", "/run/van-dashboard")
ACTIVE_MARKER = os.path.join(RUNTIME_DIR, "cop-alert.active")
ENGINE_MARKER = os.path.join(RUNTIME_DIR, "engine-running")

TUYA_TOGGLE = os.environ.get("VAN_DASHBOARD_TUYA_TOGGLE", "/home/pi/scripts/tuya_toggle.sh")
TUYA_STATUS = os.environ.get("VAN_DASHBOARD_TUYA_STATUS", "/home/pi/scripts/tuya_status.sh")
NTFY_SEND = os.environ.get("VAN_DASHBOARD_NTFY_SEND", "/home/pi/scripts/ntfy_send.sh")
TUYA_LIGHT = os.environ.get("VAN_DASHBOARD_TUYA_LIGHT", "/home/pi/scripts/tuya_light.sh")
POLICYCTL = "/home/pi/scripts/policyctl"
CONNECTIVITY_STATUS = os.environ.get(
    "VAN_DASHBOARD_CONNECTIVITY_STATUS", "/home/pi/scripts/connectivity_status.py"
)
UBNT_WIFI_TOOL = os.environ.get(
    "VAN_DASHBOARD_UBNT_WIFI_TOOL", "/home/pi/scripts/ubnt_wifi.py"
)
SPEEDTEST = os.path.expanduser(
    os.environ.get("VAN_DASHBOARD_SPEEDTEST", "/home/pi/scripts/speedtest.sh")
)
CONNECTIVITY_INTERVAL = float(os.environ.get("VAN_DASHBOARD_CONNECTIVITY_INTERVAL", "30"))
SPEEDTEST_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_SPEEDTEST_TIMEOUT", "180"))
TUYA_POLL_INTERVAL = float(os.environ.get("VAN_DASHBOARD_TUYA_POLL_INTERVAL", "15"))
POLICYCTL_TIMEOUT = 15
COP_LED_TARGET = os.environ.get("VAN_DASHBOARD_COP_LED_TARGET", "light.ext_led")
# Captured from solder_led on 2026-07-18. COP ALERT deliberately uses this
# fixed look; it does not query or depend on solder_led at activation time.
COP_LED_BRIGHTNESS = 255
COP_LED_COLOR_TEMP_KELVIN = 2702
COP_LED_RETRY_INTERVAL = float(os.environ.get("VAN_DASHBOARD_COP_LED_RETRY_INTERVAL", "5"))
COP_LED_VERIFY_INTERVAL = float(
    os.environ.get("VAN_DASHBOARD_COP_LED_VERIFY_INTERVAL", "30")
)
COP_LED_CONNECT_GRACE = float(os.environ.get("VAN_DASHBOARD_COP_LED_CONNECT_GRACE", "90"))

CAN_CHANNEL = os.environ.get("VAN_DASHBOARD_CAN_CHANNEL", "can0")
CAN_BITRATE = 500000
# Two independent C-CAN engine-speed broadcasts. 0x0FC varies naturally around
# idle while 0x0F4 held the same steady 752 RPM value in the reference capture;
# requiring both avoids treating either field alone as proof.
ENGINE_FRAME_DIVISORS = {0x0F4: 8.0, 0x0FC: 4.0}
ENGINE_ACTUAL_FRAME_ID = 0x0FC
ENGINE_RUNNING_MIN_RPM = 300.0
ENGINE_RUNNING_MAX_RPM = 8000.0
ENGINE_CONFIRM_FRAMES = 5
ENGINE_EVIDENCE_MAX_AGE = 2.5
CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_SFF_MASK = 0x7FF

# An RF Hub identification read is a verified, non-actuating C-CAN wake.  Its
# network-management wake powers the dash accessory rail for roughly 30-60 s.
RFH_TXID = 0x18DAC7F1
RFH_RXID = 0x18DAF1C7
RFH_WAKE_REQUEST = bytes((0x22, 0xF1, 0x90))
WAKE_INTERVAL = float(os.environ.get("VAN_DASHBOARD_WAKE_INTERVAL", "15"))
NTFY_INTERVAL = float(os.environ.get("VAN_DASHBOARD_NTFY_INTERVAL", "300"))
NTFY_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_NTFY_TIMEOUT", "20"))
FLOOD_CHECK_INTERVAL = float(os.environ.get("VAN_DASHBOARD_FLOOD_CHECK_INTERVAL", "15"))

DEFAULT_SONOS_DEVICE = os.environ.get("VAN_DASHBOARD_SONOS_DEVICE", "vonFront")
SONOS_ART_TIMEOUT = 5
SONOS_ART_MAX_BYTES = 2 * 1024 * 1024

LIGHT_GROUPS = (
    (
        "cab",
        "Cab",
        (
            ("light.wiz_front_driver", "Driver"),
            ("light.wiz_front_passenger", "Passenger"),
        ),
    ),
    (
        "rear",
        "Rear",
        (
            ("light.wiz_dresser", "Dresser"),
            ("light.wiz_werkbench", "Workbench"),
        ),
    ),
    ("kitchen", "Kitchen", (("light.wiz_kitchen", "Kitchen"),)),
    ("exterior", "Exterior", (("light.ext_led", "Exterior LED"),)),
    ("solder", "Solder", (("light.solder_led", "Solder LED"),)),
    (
        "extra",
        "Extra",
        (
            ("light.extra_led_1", "LED 1"),
            ("light.extra_led_2", "LED 2"),
        ),
    ),
)
LIGHT_COMMAND_TIMEOUT = 20


def atomic_json_write(path, value):
    """Write JSON without leaving a partially-written state file."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=1, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


class StateStore:
    def __init__(self, path=STATE_PATH):
        self.path = path
        self.lock = threading.RLock()
        try:
            with open(path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            self.data = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("cop_alert", False)
        self.data.setdefault("sonos_device", None)

    def get(self, key, default=None):
        with self.lock:
            return self.data.get(key, default)

    def set(self, key, value):
        with self.lock:
            self.data[key] = value
            atomic_json_write(self.path, self.data)


def rpm_from_engine_frame(can_id, data):
    """Decode the verified C-CAN engine-speed broadcast, or return None.

    2026-07 labeled captures on this van showed both fields at 0 with ignition
    on/engine stopped. At idle, 0x0F4 was 0x1780 (6016 / 8 = 752 RPM) while
    0x0FC varied around 0x0BC0 (3008 / 4 = 752 RPM).
    """
    divisor = ENGINE_FRAME_DIVISORS.get(can_id)
    if divisor is None or len(data) < 2:
        return None
    return ((data[0] << 8) | data[1]) / divisor


class EngineMonitor:
    """Passively track fresh engine-running evidence from C-CAN."""

    def __init__(self, channel=CAN_CHANNEL, clock=time.monotonic):
        self.channel = channel
        self.clock = clock
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.last_frame_at = None
        self.last_running_at = {can_id: None for can_id in ENGINE_FRAME_DIVISORS}
        self.last_rpm = {can_id: None for can_id in ENGINE_FRAME_DIVISORS}
        self.confirmations = {can_id: 0 for can_id in ENGINE_FRAME_DIVISORS}
        self.error = "waiting for C-CAN engine-speed frames"

    def start(self):
        if not self.thread:
            self.thread = threading.Thread(target=self._loop, name="engine-monitor", daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()

    def observe(self, can_id, data, observed_at=None):
        """Record one frame. Public so the decoder can be tested offline."""
        rpm = rpm_from_engine_frame(can_id, data)
        if rpm is None:
            return
        now = self.clock() if observed_at is None else observed_at
        with self.lock:
            self.last_frame_at = now
            self.last_rpm[can_id] = rpm
            self.error = None
            if ENGINE_RUNNING_MIN_RPM <= rpm <= ENGINE_RUNNING_MAX_RPM:
                self.confirmations[can_id] = min(
                    ENGINE_CONFIRM_FRAMES, self.confirmations[can_id] + 1
                )
                if self.confirmations[can_id] >= ENGINE_CONFIRM_FRAMES:
                    self.last_running_at[can_id] = now
            else:
                self.confirmations[can_id] = 0
                self.last_running_at[can_id] = None

    def snapshot(self):
        now = self.clock()
        with self.lock:
            running_ages = [
                now - self.last_running_at[can_id]
                for can_id in ENGINE_FRAME_DIVISORS
                if self.last_running_at[can_id] is not None
            ]
            running_age = max(running_ages) if len(running_ages) == len(ENGINE_FRAME_DIVISORS) else None
            frame_age = None if self.last_frame_at is None else now - self.last_frame_at
            running = running_age is not None and running_age <= ENGINE_EVIDENCE_MAX_AGE
            rpm = self.last_rpm[ENGINE_ACTUAL_FRAME_ID]
            if rpm is None:
                rpm = next((value for value in self.last_rpm.values() if value is not None), None)
            return {
                "running": running,
                "rpm": round(rpm, 1) if rpm is not None else None,
                "evidence_age_seconds": round(running_age, 2) if running_age is not None else None,
                "frame_age_seconds": round(frame_age, 2) if frame_age is not None else None,
                "source": "C-CAN 0x0F4 BE/8 + 0x0FC BE/4",
                "error": self.error,
            }

    def _loop(self):
        while not self.stop_event.is_set():
            can_socket = None
            try:
                can_socket = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
                can_socket.setsockopt(
                    socket.SOL_CAN_RAW,
                    socket.CAN_RAW_FILTER,
                    # Match only standard, non-RTR engine-speed data frames.
                    # Without the flag bits in the mask, unrelated extended
                    # frames with matching low 11 bits could be misclassified.
                    b"".join(
                        struct.pack(
                            "=II",
                            can_id,
                            CAN_EFF_FLAG | CAN_RTR_FLAG | CAN_SFF_MASK,
                        )
                        for can_id in ENGINE_FRAME_DIVISORS
                    ),
                )
                can_socket.bind((self.channel,))
                can_socket.settimeout(1.0)
                with self.lock:
                    self.error = None
                while not self.stop_event.is_set():
                    try:
                        frame = can_socket.recv(16)
                    except socket.timeout:
                        continue
                    can_id, dlc, payload = struct.unpack("=IB3x8s", frame)
                    if can_id & (CAN_EFF_FLAG | CAN_RTR_FLAG):
                        continue
                    self.observe(can_id & CAN_SFF_MASK, payload[:dlc])
            except OSError as exc:
                with self.lock:
                    self.error = f"{self.channel} receive unavailable: {exc}"
                self.stop_event.wait(2.0)
            finally:
                if can_socket is not None:
                    can_socket.close()


def run_command(args, timeout=20, input_text=None):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        input=input_text,
    )


def c_can_link_status(command=run_command):
    """Confirm the shared interface is already safe for the requested C-CAN TX."""
    try:
        result = command(["/usr/sbin/ip", "-details", "link", "show", CAN_CHANNEL], timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"cannot inspect {CAN_CHANNEL}: {exc}"
    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode:
        return False, f"{CAN_CHANNEL} is unavailable"
    if not re.search(r"\bstate UP\b|<[^>]*\bUP\b[^>]*>", output):
        return False, f"{CAN_CHANNEL} is down"
    match = re.search(r"\bbitrate\s+(\d+)", output)
    bitrate = int(match.group(1)) if match else None
    if bitrate != CAN_BITRATE:
        return False, f"{CAN_CHANNEL} is not C-CAN speed (found {bitrate or 'unknown'} bit/s)"
    if "LISTEN-ONLY" in output:
        return False, f"{CAN_CHANNEL} is listen-only; shared interface was not reconfigured"
    return True, f"{CAN_CHANNEL} armed at {CAN_BITRATE} bit/s"


def send_c_can_wake(command=run_command):
    """Send one self-validating RF Hub identification read.

    A response (positive or negative) proves that the addressed exchange occurred.
    No interface settings are changed here.
    """
    ready, detail = c_can_link_status(command)
    if not ready:
        return False, detail
    wake_socket = None
    try:
        import isotp  # lazy: the dashboard and Sonos UI can still start if CAN tooling is absent

        wake_socket = isotp.socket()
        wake_socket.set_fc_opts(stmin=0, bs=0)
        wake_socket.bind(
            CAN_CHANNEL,
            address=isotp.Address(
                isotp.AddressingMode.Normal_29bits,
                txid=RFH_TXID,
                rxid=RFH_RXID,
            ),
        )
        wake_socket.settimeout(1.5)
        wake_socket.send(RFH_WAKE_REQUEST)
        response = wake_socket.recv()
    except (ImportError, OSError, RuntimeError) as exc:
        return False, f"C-CAN wake failed: {exc}"
    except Exception as exc:  # can-isotp uses its own timeout/error classes across versions
        return False, f"C-CAN wake got no RF Hub response: {exc}"
    finally:
        if wake_socket is not None:
            try:
                wake_socket.close()
            except Exception:
                pass
    if not response:
        return False, "C-CAN wake got no RF Hub response"
    return True, "RF Hub answered; C-CAN/dashcam wake refreshed"


class CopAlertManager:
    def __init__(
        self,
        store,
        engine_monitor,
        command=run_command,
        wake=send_c_can_wake,
        runtime_dir=RUNTIME_DIR,
        clock=time.monotonic,
        wall_clock=time.time,
    ):
        self.store = store
        self.engine_monitor = engine_monitor
        self.command = command
        self.wake = wake
        self.runtime_dir = runtime_dir
        self.active_marker = os.path.join(runtime_dir, os.path.basename(ACTIVE_MARKER))
        self.engine_marker = os.path.join(runtime_dir, os.path.basename(ENGINE_MARKER))
        self.clock = clock
        self.wall_clock = wall_clock
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.ext_flood = "unknown"
        self.errors = {}
        self.last_wake = None
        self.last_wake_ok = None
        self.last_wake_message = "not attempted"
        self.last_ntfy = None
        self.ntfy_pending = False
        self.ntfy_thread = None
        self.next_wake = 0.0
        self.next_flood_check = 0.0
        self.next_ntfy = 0.0

    def start(self):
        os.makedirs(self.runtime_dir, mode=0o750, exist_ok=True)
        self.engine_monitor.start()
        if not self.thread:
            self.thread = threading.Thread(target=self._loop, name="cop-alert", daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.engine_monitor.stop()

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
                self.next_wake = 0.0
                self.next_flood_check = 0.0
                if not was_active:
                    self.next_ntfy = 0.0
            else:
                self._remove(self.active_marker)
                self._remove(self.engine_marker)

        # Begin the activation notification before touching the Wi-Fi Tuya
        # device. The single-flight worker keeps a slow or disconnected ntfy
        # endpoint off both the request thread and the COP maintenance loop.
        if active and not was_active:
            self._queue_ntfy(self.clock())

        # Give the button immediate, deterministic switch behavior.  Background
        # retries and status reporting handle a temporarily unavailable HA API.
        desired = "on" if active else "off"
        ok, message = self._set_ext_flood(desired)
        if not ok:
            self._set_error("ext_flood", message)
        return self.snapshot()

    def snapshot(self):
        engine = self.engine_monitor.snapshot()
        with self.lock:
            last_error = "; ".join(self.errors.values()) or None
            return {
                "active": self.active,
                "engine": engine,
                "ext_flood": self.ext_flood,
                "last_wake": self.last_wake,
                "last_wake_ok": self.last_wake_ok,
                "last_wake_message": self.last_wake_message,
                "last_ntfy": self.last_ntfy,
                "last_error": last_error,
                "wake_mode": (
                    f"C-CAN RF Hub 22 F190 every {WAKE_INTERVAL:g} seconds while engine-stopped"
                ),
            }

    def tick(self):
        """Run one manager iteration; separated for focused tests."""
        now = self.clock()
        if not self.active:
            self._remove(self.active_marker)
            self._remove(self.engine_marker)
            if now >= self.next_flood_check:
                self.next_flood_check = now + FLOOD_CHECK_INTERVAL
                self._get_ext_flood()
            return

        self._touch(self.active_marker)
        engine = self.engine_monitor.snapshot()
        if engine["running"]:
            self._touch(self.engine_marker)
        else:
            self._remove(self.engine_marker)

        if now >= self.next_flood_check:
            self.next_flood_check = now + FLOOD_CHECK_INTERVAL
            desired = "off" if engine["running"] else "on"
            actual = self._get_ext_flood()
            if actual != desired:
                ok, message = self._set_ext_flood(desired)
                if not ok:
                    self._set_error("ext_flood", message)

        # A running engine is already broadcasting and powers the dashcam. Do
        # not add diagnostic traffic; resume parked wakes if the engine stops.
        if not engine["running"] and now >= self.next_wake:
            ok, message = self.wake()
            with self.lock:
                self.last_wake = int(self.wall_clock())
                self.last_wake_ok = bool(ok)
                self.last_wake_message = message
            self._set_error("can_wake", None if ok else message)
            self.next_wake = now + (WAKE_INTERVAL if ok else min(5.0, WAKE_INTERVAL))

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
        engine_monitor=None,
        target=COP_LED_TARGET,
        command=run_command,
        clock=time.monotonic,
        wall_clock=time.time,
        retry_interval=COP_LED_RETRY_INTERVAL,
        verify_interval=COP_LED_VERIFY_INTERVAL,
        connect_grace=COP_LED_CONNECT_GRACE,
        brightness=COP_LED_BRIGHTNESS,
        color_temp_kelvin=COP_LED_COLOR_TEMP_KELVIN,
    ):
        self.store = store
        self.engine_monitor = engine_monitor
        self.target = target
        self.command = command
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

        if self.engine_monitor is not None and self.engine_monitor.snapshot()["running"]:
            with self.lock:
                self.connect_started_at = None
            self._schedule(
                "paused",
                "Engine running · ext_flood is intentionally off",
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


class TuyaSwitchManager:
    """Cache and safely toggle one Home Assistant/Tuya switch."""

    def __init__(
        self,
        entity,
        command=run_command,
        interval=TUYA_POLL_INTERVAL,
        wall_clock=time.time,
    ):
        self.entity = entity
        self.command = command
        self.interval = interval
        self.wall_clock = wall_clock
        self.lock = threading.Lock()
        self.operation_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.state = "unknown"
        self.checked_at = None
        self.last_error = None
        self.refreshing = False
        self.changing = False

    def start(self):
        if not self.thread:
            self.thread = threading.Thread(
                target=self._loop, name=f"tuya-{self.entity}", daemon=True
            )
            self.thread.start()

    def stop(self):
        self.stop_event.set()

    def snapshot(self):
        with self.lock:
            return {
                "entity": self.entity,
                "state": self.state,
                "available": self.state in ("on", "off"),
                "checked_at": self.checked_at,
                "last_error": self.last_error,
                "refreshing": self.refreshing,
                "changing": self.changing,
            }

    def refresh(self):
        with self.operation_lock:
            return self._refresh()

    def toggle(self):
        with self.operation_lock:
            with self.lock:
                current = self.state
                if current not in ("on", "off"):
                    raise ValueError(f"{self.entity} status is unavailable")
                desired = "off" if current == "on" else "on"
                self.state = "unknown"
                self.changing = True
                self.last_error = None
            try:
                result = self.command([TUYA_TOGGLE, self.entity, desired], timeout=20)
            except (OSError, subprocess.TimeoutExpired) as exc:
                message = f"could not turn {self.entity} {desired}: {exc}"
                self._mark_unknown(message, changing=False)
                raise RuntimeError(message) from exc
            if result.returncode:
                detail = (result.stderr or result.stdout or "command failed").strip()
                message = f"could not turn {self.entity} {desired}: {detail[-300:]}"
                self._mark_unknown(message, changing=False)
                raise RuntimeError(message)

            # Read the authoritative Tuya/HA state after the service call. If
            # that verification fails, the UI returns to neutral grey rather
            # than presenting the requested state as confirmed.
            status = self._refresh()
            if not status["available"]:
                raise RuntimeError(f"{self.entity} changed but status verification failed")
            return status

    def _refresh(self):
        with self.lock:
            self.refreshing = True
        try:
            result = self.command([TUYA_STATUS, self.entity], timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._mark_unknown(f"could not read {self.entity}: {exc}", changing=False)
        else:
            state = result.stdout.strip().lower()
            if result.returncode == 0 and state in ("on", "off"):
                with self.lock:
                    self.state = state
                    self.checked_at = int(self.wall_clock())
                    self.last_error = None
                    self.refreshing = False
                    self.changing = False
            else:
                detail = (result.stderr or result.stdout or "status unavailable").strip()
                self._mark_unknown(f"could not read {self.entity}: {detail[-300:]}", changing=False)
        return self.snapshot()

    def _mark_unknown(self, message, changing):
        with self.lock:
            self.state = "unknown"
            self.checked_at = int(self.wall_clock())
            self.last_error = message
            self.refreshing = False
            self.changing = changing

    def _loop(self):
        while not self.stop_event.is_set():
            self.refresh()
            self.stop_event.wait(max(1.0, self.interval))


class LightingCommandError(RuntimeError):
    pass


class LightingController:
    """Strict Home Assistant boundary for the dashboard's configured lights."""

    VALID_STATES = {"on", "off", "unavailable", "unknown"}

    def __init__(self, command=run_command, timeout=LIGHT_COMMAND_TIMEOUT):
        self.command = command
        self.timeout = timeout
        self.operation_lock = threading.Lock()
        ordered_entities = tuple(
            entity
            for _group_id, _group_label, lights in LIGHT_GROUPS
            for entity, _label in lights
        )
        self.ordered_entities = ordered_entities
        self.entities = set(ordered_entities)
        self.targets = {"all": ordered_entities}
        self.targets.update(
            {
                f"group:{group_id}": tuple(entity for entity, _label in lights)
                for group_id, _group_label, lights in LIGHT_GROUPS
            }
        )
        self.targets.update({entity: (entity,) for entity in self.entities})

    @classmethod
    def parse_status(cls, output):
        try:
            values = json.loads(output)
        except (TypeError, json.JSONDecodeError) as exc:
            raise LightingCommandError(f"Home Assistant returned invalid JSON: {exc}") from exc
        if not isinstance(values, list):
            raise LightingCommandError("Home Assistant returned an unexpected light schema")
        parsed = {}
        for item in values:
            if not isinstance(item, dict) or set(item) != {
                "entity_id",
                "state",
                "brightness",
            }:
                raise LightingCommandError("Home Assistant returned an unexpected light schema")
            entity = item["entity_id"]
            state = item["state"]
            brightness = item["brightness"]
            if not isinstance(entity, str) or not re.fullmatch(r"light\.[a-z0-9_]+", entity):
                raise LightingCommandError("Home Assistant returned an invalid light entity")
            if state not in cls.VALID_STATES:
                state = "unknown"
            if brightness is not None and (
                type(brightness) is not int or not 0 <= brightness <= 255
            ):
                raise LightingCommandError(
                    f"Home Assistant returned invalid brightness for {entity}"
                )
            if entity in parsed:
                raise LightingCommandError(f"Home Assistant returned duplicate {entity}")
            parsed[entity] = {"state": state, "brightness": brightness}
        return parsed

    @staticmethod
    def aggregate(lights):
        states = [light["state"] for light in lights]
        if states and all(state == "on" for state in states):
            return "on"
        if states and all(state == "off" for state in states):
            return "off"
        if any(state in ("on", "off") for state in states):
            return "mixed"
        return "unknown"

    def _run(self, args, expect_status=False):
        try:
            result = self.command(args, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise LightingCommandError(
                f"lighting command timed out after {self.timeout:g} seconds"
            ) from exc
        except OSError as exc:
            raise LightingCommandError(f"could not start lighting command: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or "lighting command failed").strip()
            raise LightingCommandError(detail[-300:])
        return self.parse_status(result.stdout) if expect_status else None

    def status(self):
        try:
            observed = self._run([TUYA_LIGHT, "list"], expect_status=True)
        except LightingCommandError as exc:
            # A dashboard-only deployment used to omit tuya_light.sh. Keep
            # status useful with that older helper, while the deployment tool
            # now installs and health-checks both files as one unit.
            if "usage: tuya_light.sh" not in str(exc):
                raise
            observed = self._legacy_status()
        groups = []
        all_lights = []
        for group_id, group_label, configured in LIGHT_GROUPS:
            lights = []
            for entity, label in configured:
                value = observed.get(entity, {"state": "unknown", "brightness": None})
                brightness = value["brightness"]
                light = {
                    "entity_id": entity,
                    "label": label,
                    "state": value["state"],
                    "available": value["state"] in ("on", "off"),
                    "brightness": (
                        round(brightness * 100 / 255) if brightness is not None else None
                    ),
                }
                lights.append(light)
                all_lights.append(light)
            groups.append(
                {
                    "id": group_id,
                    "label": group_label,
                    "state": self.aggregate(lights),
                    "lights": lights,
                }
            )
        return {
            "state": self.aggregate(all_lights),
            "on_count": sum(light["state"] == "on" for light in all_lights),
            "available_count": sum(light["available"] for light in all_lights),
            "total_count": len(all_lights),
            "groups": groups,
        }

    def _legacy_status(self):
        observed = {}
        successful = 0
        for entity in self.ordered_entities:
            try:
                result = self.command(
                    [TUYA_LIGHT, "status", entity], timeout=self.timeout
                )
            except (OSError, subprocess.TimeoutExpired):
                result = None
            if result is None or result.returncode:
                observed[entity] = {"state": "unknown", "brightness": None}
                continue
            try:
                item = json.loads(result.stdout)
            except (TypeError, json.JSONDecodeError):
                item = None
            state = item.get("state") if isinstance(item, dict) else None
            brightness = item.get("brightness") if isinstance(item, dict) else None
            if state not in self.VALID_STATES or (
                brightness is not None
                and (type(brightness) is not int or not 0 <= brightness <= 255)
            ):
                observed[entity] = {"state": "unknown", "brightness": None}
                continue
            observed[entity] = {"state": state, "brightness": brightness}
            successful += 1
        if not successful:
            raise LightingCommandError(
                "lighting helper is outdated and individual status queries failed"
            )
        return observed

    def set_power(self, target, enabled):
        entities = self.targets.get(target)
        if entities is None:
            raise ValueError("unknown lighting target")
        if type(enabled) is not bool:
            raise ValueError("lighting power value must be boolean")
        with self.operation_lock:
            for entity in entities:
                self._run(
                    [TUYA_TOGGLE, entity, "on" if enabled else "off"],
                    expect_status=False,
                )
            return self.status()

    def set_brightness(self, entity, brightness):
        if entity not in self.entities:
            raise ValueError("unknown light entity")
        if type(brightness) is not int or not 1 <= brightness <= 100:
            raise ValueError("brightness must be from 1 to 100")
        raw_brightness = max(1, round(brightness * 255 / 100))
        with self.operation_lock:
            self._run(
                [TUYA_LIGHT, "set", entity, str(raw_brightness)],
                expect_status=False,
            )
            return self.status()


class PolicyCommandError(RuntimeError):
    pass


class StoragePolicyManager:
    """Strict policyctl boundary for requested and observed storage state."""

    TARGETS = {
        "disks_enabled": "disks",
        "torrents_enabled": "torrents",
        "allow_starlink_torrents": "starlink-torrents",
    }
    STATUS_FIELDS = {"version", *TARGETS, "runtime"}
    RUNTIME_FIELDS = {
        "disks_mounted",
        "mounted_disk_labels",
        "qbittorrent_running",
    }

    def __init__(self, command=run_command, timeout=POLICYCTL_TIMEOUT):
        self.command = command
        self.timeout = timeout

    @classmethod
    def parse_status(cls, output):
        try:
            status = json.loads(output)
        except (TypeError, json.JSONDecodeError) as exc:
            raise PolicyCommandError(f"policyctl returned invalid JSON: {exc}") from exc
        if not isinstance(status, dict) or set(status) != cls.STATUS_FIELDS:
            raise PolicyCommandError("policyctl returned an unexpected status schema")
        if type(status["version"]) is not int or status["version"] != 1:
            raise PolicyCommandError("policyctl returned an unsupported policy version")
        for field in cls.TARGETS:
            if type(status[field]) is not bool:
                raise PolicyCommandError(f"policyctl field {field} was not boolean")
        runtime = status["runtime"]
        if not isinstance(runtime, dict) or set(runtime) != cls.RUNTIME_FIELDS:
            raise PolicyCommandError("policyctl returned an unexpected runtime schema")
        for field in ("disks_mounted", "qbittorrent_running"):
            if type(runtime[field]) is not bool:
                raise PolicyCommandError(
                    f"policyctl runtime field {field} was not boolean"
                )
        labels = runtime["mounted_disk_labels"]
        if (
            not isinstance(labels, list)
            or any(not isinstance(label, str) or not label for label in labels)
            or len(labels) != len(set(labels))
        ):
            raise PolicyCommandError(
                "policyctl runtime mounted_disk_labels was invalid"
            )
        if runtime["disks_mounted"] is not bool(labels):
            raise PolicyCommandError("policyctl returned inconsistent disk runtime state")
        return status

    def _run(self, args, expect_json):
        try:
            result = self.command(args, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise PolicyCommandError(
                f"policyctl timed out after {self.timeout:g} seconds"
            ) from exc
        except OSError as exc:
            raise PolicyCommandError(f"could not start policyctl: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or "policyctl failed").strip()
            raise PolicyCommandError(detail[-300:])
        return self.parse_status(result.stdout) if expect_json else None

    def status(self):
        return self._run([POLICYCTL, "--json", "status"], expect_json=True)

    def update(self, field, enabled):
        target = self.TARGETS.get(field)
        if target is None:
            raise ValueError("unknown policy field")
        if type(enabled) is not bool:
            raise ValueError("policy value must be boolean")
        requested = self._run(
            [POLICYCTL, "--json", target, "on" if enabled else "off"],
            expect_json=True,
        )
        if requested[field] is not enabled:
            raise PolicyCommandError(f"policyctl did not confirm {field}")
        return self.status()

    def reconcile(self):
        self._run([POLICYCTL, "reconcile"], expect_json=False)


class SonosController:
    """Small Sonos grouping/volume controller matching the audiobook page."""

    def __init__(self, store, discover_func=None, clock=time.monotonic, art_opener=None):
        self.store = store
        self.discover_func = discover_func
        self.clock = clock
        self.art_opener = art_opener or urlopen
        self.lock = threading.Lock()
        self.zones = {}
        self.zones_at = 0.0

    def get_zones(self, force=False):
        with self.lock:
            if self.zones and not force and self.clock() - self.zones_at < 600:
                return self.zones
            if self.discover_func is None:
                from soco.discovery import discover

                found = discover(timeout=5) or set()
            else:
                found = self.discover_func(timeout=5) or set()
            fresh = {zone.player_name: zone for zone in found if zone.is_visible}
            if fresh:
                self.zones = fresh
                self.zones_at = self.clock()
            return self.zones

    def coordinator(self):
        zones = self.get_zones()
        if not zones:
            zones = self.get_zones(force=True)
        if not zones:
            raise RuntimeError("no Sonos speakers found")
        remembered = self.store.get("sonos_device")
        if remembered in zones:
            return zones[remembered].group.coordinator
        for zone in zones.values():
            if zone.group.coordinator != zone:
                continue
            try:
                state = zone.get_current_transport_info()["current_transport_state"]
            except Exception:
                continue
            if state == "PLAYING":
                return zone
        if DEFAULT_SONOS_DEVICE in zones:
            return zones[DEFAULT_SONOS_DEVICE].group.coordinator
        return next(iter(zones.values())).group.coordinator

    def snapshot(self):
        zones = self.get_zones()
        if not zones:
            raise RuntimeError("no Sonos speakers found")
        coordinator = self.coordinator()
        coordinator_name = coordinator.player_name
        members = {member.player_name for member in coordinator.group.members}
        try:
            group_volume = coordinator.group.volume
        except Exception:
            group_volume = None
        try:
            group_muted = coordinator.group.mute
        except Exception:
            group_muted = None
        try:
            transport_state = coordinator.get_current_transport_info()[
                "current_transport_state"
            ]
        except Exception:
            transport_state = "UNKNOWN"
        try:
            track = coordinator.get_current_track_info() or {}
        except Exception:
            track = {}
        now_playing = {
            "title": track.get("title") or track.get("radio_show") or "Nothing playing",
            "artist": track.get("artist") or track.get("album") or "",
            "album": track.get("album") or "",
            "position": track.get("position") or "",
            "duration": track.get("duration") or "",
            "transport_state": transport_state,
            "album_art": self.album_art_path(track.get("album_art")),
        }
        speakers = []
        for name, zone in sorted(zones.items()):
            try:
                volume = zone.volume
            except Exception:
                volume = None
            try:
                muted = zone.mute
            except Exception:
                muted = None
            speakers.append(
                {
                    "name": name,
                    "volume": volume,
                    "muted": muted,
                    "grouped": name in members,
                    "coordinator": name == coordinator_name,
                    "group_coordinator": zone.group.coordinator.player_name,
                }
            )
        return {
            "ok": True,
            "coordinator": coordinator_name,
            "group": {"volume": group_volume, "muted": group_muted},
            "now_playing": now_playing,
            "speakers": speakers,
        }

    @staticmethod
    def album_art_path(art_url):
        if not isinstance(art_url, str) or not art_url:
            return None
        key = hashlib.sha256(art_url.encode("utf-8")).hexdigest()[:16]
        return f"/api/speakers/art/{key}"

    def album_art(self, key):
        coordinator = self.coordinator()
        track = coordinator.get_current_track_info() or {}
        art_url = track.get("album_art")
        if not art_url or self.album_art_path(art_url).rsplit("/", 1)[-1] != key:
            raise KeyError("album art is no longer current")
        parsed = urlsplit(art_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != coordinator.ip_address
            or parsed.port != 1400
        ):
            raise ValueError("Sonos returned an unexpected album-art URL")
        request_object = Request(art_url, headers={"User-Agent": "van-dashboard/1"})
        with self.art_opener(request_object, timeout=SONOS_ART_TIMEOUT) as response:
            content = response.read(SONOS_ART_MAX_BYTES + 1)
        if len(content) > SONOS_ART_MAX_BYTES:
            raise ValueError("Sonos album art exceeded the size limit")
        signatures = (
            (b"\xff\xd8\xff", "image/jpeg"),
            (b"\x89PNG\r\n\x1a\n", "image/png"),
            (b"GIF87a", "image/gif"),
            (b"GIF89a", "image/gif"),
        )
        content_type = next(
            (mime for signature, mime in signatures if content.startswith(signature)),
            None,
        )
        if content_type is None and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            content_type = "image/webp"
        if content_type is None:
            raise ValueError("Sonos album art was not a supported image")
        return content, content_type

    def select(self, name):
        zones = self.get_zones()
        if name not in zones:
            raise KeyError(f"unknown speaker '{name}'")
        coordinator = zones[name].group.coordinator
        self.store.set("sonos_device", coordinator.player_name)
        return coordinator.player_name

    def group(self, name, grouped):
        zones = self.get_zones()
        if name not in zones:
            raise KeyError(f"unknown speaker '{name}'")
        coordinator = self.coordinator()
        speaker = zones[name]
        if not grouped and speaker.player_name == coordinator.player_name:
            raise ValueError("select another group before removing its coordinator")
        members = {member.player_name for member in coordinator.group.members}
        if grouped and name not in members:
            speaker.join(coordinator)
            message = f"added {name} to {coordinator.player_name}"
        elif not grouped and name in members:
            speaker.unjoin()
            message = f"removed {name} from {coordinator.player_name}"
        else:
            message = f"{name} group is unchanged"
        self.get_zones(force=True)
        return message

    def set_volume(self, name, volume):
        zones = self.get_zones()
        if name not in zones:
            raise KeyError(f"unknown speaker '{name}'")
        volume = max(0, min(100, int(volume)))
        zones[name].volume = volume
        return volume

    def set_mute(self, name, muted):
        zones = self.get_zones()
        if name not in zones:
            raise KeyError(f"unknown speaker '{name}'")
        zones[name].mute = bool(muted)
        return bool(muted)

    def set_group_volume(self, volume):
        volume = max(0, min(100, int(volume)))
        self.coordinator().group.volume = volume
        return volume

    def set_group_mute(self, muted):
        muted = bool(muted)
        self.coordinator().group.mute = muted
        return muted

    def transport(self, action):
        if action not in ("play_pause", "previous", "next"):
            raise ValueError("unknown Sonos transport action")
        coordinator = self.coordinator()
        if action == "play_pause":
            state = coordinator.get_current_transport_info()["current_transport_state"]
            if state == "PLAYING":
                coordinator.pause()
                return "Sonos paused"
            coordinator.play()
            return "Sonos playing"
        if action == "previous":
            coordinator.previous()
            return "Previous Sonos track"
        if action == "next":
            coordinator.next()
            return "Next Sonos track"


class ConnectivityMonitor:
    """Cache the reusable connectivity collector away from HTTP request threads."""

    def __init__(
        self,
        collector=CONNECTIVITY_STATUS,
        interval=CONNECTIVITY_INTERVAL,
        command=run_command,
        clock=time.monotonic,
        wall_clock=time.time,
    ):
        self.collector = collector
        self.interval = interval
        self.command = command
        self.clock = clock
        self.wall_clock = wall_clock
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.refresh_event = threading.Event()
        self.thread = None
        self.refreshing = False
        self.last_error = None
        self.data = {
            "checked_at": None,
            "internet": {"online": None, "source": "mwan3 reachability tracking"},
            "router": {
                "reachable": None,
                "mode": None,
                "online": [],
                "interfaces": [],
                "error": None,
            },
            "ubnt": {
                "reachable": None,
                "connected": None,
                "ssid": None,
                "signal_dbm": None,
                "noise_dbm": None,
                "quality_percent": None,
                "ccq_percent": None,
                "bitrate": None,
                "error": None,
            },
        }

    def start(self):
        if not self.thread:
            self.thread = threading.Thread(
                target=self._loop, name="connectivity-monitor", daemon=True
            )
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.refresh_event.set()

    def request_refresh(self):
        """Wake the collector without doing router I/O in the request thread."""
        self.refresh_event.set()

    def refresh(self):
        with self.lock:
            self.refreshing = True
        try:
            result = self.command([self.collector], timeout=25)
            if result.returncode:
                detail = (result.stderr or result.stdout or "collector failed").strip()
                raise RuntimeError(detail[-300:])
            payload = json.loads(result.stdout)
            if not isinstance(payload, dict) or not all(
                key in payload for key in ("checked_at", "internet", "router", "ubnt")
            ):
                raise ValueError("collector returned an incomplete payload")
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            with self.lock:
                self.last_error = str(exc)
        else:
            with self.lock:
                self.data = payload
                self.last_error = None
        finally:
            with self.lock:
                self.refreshing = False
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            data = copy.deepcopy(self.data)
            data["refreshing"] = self.refreshing
            data["last_error"] = self.last_error
        checked_at = data.get("checked_at")
        data["stale"] = checked_at is None or (
            self.wall_clock() - checked_at > max(60.0, self.interval * 2.5)
        )
        return data

    def _loop(self):
        while not self.stop_event.is_set():
            started = self.clock()
            self.refresh()
            remaining = max(1.0, self.interval - (self.clock() - started))
            self.refresh_event.wait(remaining)
            self.refresh_event.clear()


class UbntWifiController:
    """Run scans and network changes off-thread through the reusable JSON tool."""

    TIMEOUTS = {
        "status": 20,
        "scan": 45,
        "connect": 260,
        "provision": 260,
        "resume": 20,
    }

    def __init__(
        self,
        tool=UBNT_WIFI_TOOL,
        command=run_command,
        wall_clock=time.time,
        on_change=None,
    ):
        self.tool = tool
        self.command = command
        self.wall_clock = wall_clock
        self.on_change = on_change
        self.lock = threading.Lock()
        self.thread = None
        self.wifi = {
            "version": 1,
            "reachable": None,
            "checked_at": None,
            "state": {
                "configured_ssid": None,
                "associated_ssid": None,
                "ccq_percent": None,
                "automatic_paused": None,
                "selector_running": None,
            },
            "profiles": [],
            "networks": [],
        }
        self.operation = {
            "status": "idle",
            "kind": None,
            "started_at": None,
            "completed_at": None,
            "message": None,
            "error": None,
        }

    def snapshot(self):
        with self.lock:
            return {
                "wifi": copy.deepcopy(self.wifi),
                "operation": dict(self.operation),
            }

    def request_refresh(self, max_age=20):
        with self.lock:
            checked_at = self.wifi.get("checked_at")
            running = self.operation["status"] == "running"
            completed_at = self.operation.get("completed_at")
        recent_attempt = completed_at is not None and (
            self.wall_clock() - completed_at <= max_age
        )
        if not running and (
            (checked_at is None and not recent_attempt)
            or (checked_at is not None and self.wall_clock() - checked_at > max_age)
        ):
            self.start("status")

    def start(self, kind, payload=None):
        if kind not in self.TIMEOUTS:
            raise ValueError("unknown UBNT Wi-Fi operation")
        with self.lock:
            if self.operation["status"] == "running":
                return False
            self.operation = {
                "status": "running",
                "kind": kind,
                "started_at": int(self.wall_clock()),
                "completed_at": None,
                "message": None,
                "error": None,
            }
            self.thread = threading.Thread(
                target=self._run,
                args=(kind, dict(payload or {})),
                name=f"ubnt-wifi-{kind}",
                daemon=True,
            )
            self.thread.start()
            return True

    def _tool_result(self, kind, payload=None):
        input_text = json.dumps(payload, separators=(",", ":")) if payload else None
        try:
            result = self.command(
                [self.tool, "--json", kind],
                timeout=self.TIMEOUTS[kind],
                input_text=input_text,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"UBNT {kind} timed out") from exc
        except OSError as exc:
            raise RuntimeError(f"could not start UBNT {kind}: {exc}") from exc
        finally:
            input_text = None
            if payload and "password" in payload:
                payload["password"] = ""
        try:
            parsed = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("UBNT Wi-Fi tool returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("UBNT Wi-Fi tool returned invalid data")
        if result.returncode or parsed.get("ok") is not True:
            raise RuntimeError(str(parsed.get("message") or "UBNT Wi-Fi operation failed"))
        wifi = parsed.get("wifi")
        if not isinstance(wifi, dict) or wifi.get("version") != 1:
            raise RuntimeError("UBNT Wi-Fi tool returned invalid status")
        return parsed

    def _refresh_after_failure(self):
        try:
            return self._tool_result("status").get("wifi")
        except RuntimeError:
            return None

    def _run(self, kind, payload):
        error = None
        message = None
        wifi = None
        try:
            result = self._tool_result(kind, payload)
            wifi = result["wifi"]
            message = result.get("message")
            if kind in ("connect", "provision", "resume") and self.on_change:
                self.on_change()
        except RuntimeError as exc:
            error = str(exc)
            wifi = self._refresh_after_failure()
        finally:
            if "password" in payload:
                payload["password"] = ""
        with self.lock:
            if wifi is not None:
                self.wifi = wifi
            self.operation = {
                "status": "error" if error else "complete",
                "kind": kind,
                "started_at": self.operation["started_at"],
                "completed_at": int(self.wall_clock()),
                "message": message,
                "error": error,
            }


def parse_speedtest_output(output):
    values = {}
    patterns = {
        "download_mbps": r"^Download(?: Speed)?:\s*([0-9.]+)\s*(?:Mbit/s|Mbps)",
        "upload_mbps": r"^Upload(?: Speed)?:\s*([0-9.]+)\s*(?:Mbit/s|Mbps)",
        "latency_ms": r"^(?:Ping|Latency):\s*([0-9.]+)\s*ms",
    }
    for name, pattern in patterns.items():
        match = re.search(pattern, output, re.IGNORECASE | re.MULTILINE)
        if match:
            values[name] = float(match.group(1))
    return values


class SpeedTestManager:
    """Run the existing speedtest script once at a time, outside request threads."""

    def __init__(
        self,
        script=SPEEDTEST,
        command=run_command,
        timeout=SPEEDTEST_TIMEOUT,
        wall_clock=time.time,
    ):
        self.script = script
        self.command = command
        self.timeout = timeout
        self.wall_clock = wall_clock
        self.lock = threading.Lock()
        self.thread = None
        self.data = {
            "status": "idle",
            "started_at": None,
            "completed_at": None,
            "download_mbps": None,
            "upload_mbps": None,
            "latency_ms": None,
            "error": None,
        }

    def start(self):
        with self.lock:
            if self.data["status"] == "running":
                return False
            self.data = {
                "status": "running",
                "started_at": int(self.wall_clock()),
                "completed_at": None,
                "download_mbps": None,
                "upload_mbps": None,
                "latency_ms": None,
                "error": None,
            }
            self.thread = threading.Thread(target=self._run, name="speedtest", daemon=True)
            self.thread.start()
            return True

    def snapshot(self):
        with self.lock:
            return dict(self.data)

    def _run(self):
        error = None
        values = {}
        try:
            result = self.command([self.script], timeout=self.timeout)
            if result.returncode:
                detail = (result.stderr or result.stdout or "speed test failed").strip()
                error = detail[-500:]
            else:
                values = parse_speedtest_output(result.stdout)
                if len(values) != 3:
                    error = "speed test returned incomplete results"
        except subprocess.TimeoutExpired:
            error = f"speed test timed out after {self.timeout:g} seconds"
        except OSError as exc:
            error = f"could not start speed test: {exc}"

        with self.lock:
            self.data.update(values)
            self.data["status"] = "error" if error else "complete"
            self.data["completed_at"] = int(self.wall_clock())
            self.data["error"] = error


app = Flask(__name__)
state_store = StateStore()
engine_monitor = EngineMonitor()
cop_alert = CopAlertManager(state_store, engine_monitor)
cop_led = CopLedManager(state_store, engine_monitor)
sonos = SonosController(state_store)
connectivity = ConnectivityMonitor()
ubnt_wifi = UbntWifiController(on_change=connectivity.request_refresh)
speedtest = SpeedTestManager()
starlink = TuyaSwitchManager("starlink")
storage_policy = StoragePolicyManager()
lighting = LightingController()


def api_error(message, status):
    return jsonify({"ok": False, "message": str(message)}), status


def request_boolean(name):
    raw = request.values.get(name, "").strip().lower()
    if raw not in ("1", "0", "true", "false", "on", "off"):
        raise ValueError(f"{name} must be true or false")
    return raw in ("1", "true", "on")


@app.before_request
def reject_cross_origin_mutations():
    """Block browser CSRF against vehicle-control POST endpoints.

    Command-line clients without browser Origin/Referer headers remain usable.
    The custom header also forces a cross-origin fetch to preflight, and this
    server intentionally grants no cross-origin access.
    """
    if request.method != "POST":
        return None
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    if origin:
        if urlsplit(origin).netloc != request.host:
            return api_error("cross-origin control request rejected", 403)
        if request.headers.get("X-Van-Dashboard") != "1":
            return api_error("dashboard control header missing", 403)
    elif referer and urlsplit(referer).netloc != request.host:
        return api_error("cross-origin control request rejected", 403)
    return None


@app.route("/api/status")
def api_status():
    return jsonify(
        {
            "ok": True,
            "cop_alert": cop_alert.snapshot(),
            "cop_led": cop_led.snapshot(),
            "starlink": starlink.snapshot(),
        }
    )


@app.route("/api/starlink", methods=["POST"])
def api_starlink():
    try:
        status = starlink.toggle()
    except ValueError as exc:
        return api_error(exc, 503)
    except RuntimeError as exc:
        return api_error(exc, 502)
    connectivity.request_refresh()
    try:
        storage_policy.reconcile()
    except PolicyCommandError as exc:
        return api_error(
            f"Starlink power changed, but torrent policy reconciliation failed: {exc}",
            502,
        )
    return jsonify(
        {
            "ok": True,
            "message": f"Starlink power {status['state']}",
            "starlink": status,
        }
    )


@app.route("/api/storage-policy", methods=["GET", "POST"])
def api_storage_policy():
    if request.method == "POST":
        expected_form = {"field", "value"}
        if set(request.form) != expected_form or any(
            len(request.form.getlist(name)) != 1 for name in expected_form
        ):
            return api_error("storage policy requires field and boolean value", 400)
        field = request.form.get("field", "")
        raw_value = request.form.get("value", "").lower()
        if field not in StoragePolicyManager.TARGETS:
            return api_error("unknown storage policy field", 400)
        if raw_value not in ("true", "false"):
            return api_error("storage policy value must be true or false", 400)
        try:
            status = storage_policy.update(field, raw_value == "true")
        except PolicyCommandError as exc:
            return api_error(f"could not update storage policy: {exc}", 502)
        label = {
            "disks_enabled": "Disks",
            "torrents_enabled": "Torrents",
            "allow_starlink_torrents": "Starlink torrents",
        }[field]
        state = "enabled" if status[field] else "disabled"
        return jsonify(
            {
                "ok": True,
                "message": f"{label} {state}",
                "policy": status,
            }
        )
    try:
        status = storage_policy.status()
    except PolicyCommandError as exc:
        return api_error(f"could not read storage policy: {exc}", 502)
    return jsonify({"ok": True, "policy": status})


@app.route("/api/lights")
def api_lights():
    try:
        status = lighting.status()
    except LightingCommandError as exc:
        return api_error(f"could not read lights: {exc}", 502)
    response = jsonify({"ok": True, "lighting": status})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/lights/power", methods=["POST"])
def api_lights_power():
    if not _exact_form(("target", "value")):
        return api_error("lighting power requires target and boolean value", 400)
    target = request.form["target"]
    raw_value = request.form["value"].lower()
    if target not in lighting.targets:
        return api_error("unknown lighting target", 400)
    if raw_value not in ("true", "false"):
        return api_error("lighting power value must be true or false", 400)
    enabled = raw_value == "true"
    try:
        status = lighting.set_power(target, enabled)
    except LightingCommandError as exc:
        return api_error(f"could not update lights: {exc}", 502)
    return jsonify(
        {
            "ok": True,
            "message": f"Lights turned {'on' if enabled else 'off'}",
            "lighting": status,
        }
    )


@app.route("/api/lights/brightness", methods=["POST"])
def api_lights_brightness():
    if not _exact_form(("entity", "brightness")):
        return api_error("light brightness requires entity and brightness", 400)
    entity = request.form["entity"]
    if entity not in lighting.entities:
        return api_error("unknown light entity", 400)
    try:
        brightness = int(request.form["brightness"])
    except (TypeError, ValueError):
        return api_error("brightness must be from 1 to 100", 400)
    if not 1 <= brightness <= 100:
        return api_error("brightness must be from 1 to 100", 400)
    try:
        status = lighting.set_brightness(entity, brightness)
    except LightingCommandError as exc:
        return api_error(f"could not set light brightness: {exc}", 502)
    return jsonify(
        {
            "ok": True,
            "message": f"Brightness set to {brightness}%",
            "lighting": status,
        }
    )


@app.route("/api/connectivity")
def api_connectivity():
    response = jsonify({"ok": True, "connectivity": connectivity.snapshot()})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/ubnt-wifi")
def api_ubnt_wifi():
    ubnt_wifi.request_refresh()
    response = jsonify({"ok": True, **ubnt_wifi.snapshot()})
    response.headers["Cache-Control"] = "no-store"
    return response


def _exact_form(fields):
    return set(request.form) == set(fields) and all(
        len(request.form.getlist(name)) == 1 for name in fields
    )


def _start_ubnt_operation(kind, payload=None):
    if not ubnt_wifi.start(kind, payload):
        return api_error("another UBNT Wi-Fi operation is already running", 409)
    return (
        jsonify(
            {
                "ok": True,
                "message": f"UBNT {kind} started",
                **ubnt_wifi.snapshot(),
            }
        ),
        202,
    )


@app.route("/api/ubnt-wifi/scan", methods=["POST"])
def api_ubnt_wifi_scan():
    if request.form:
        return api_error("UBNT scan does not accept input", 400)
    return _start_ubnt_operation("scan")


@app.route("/api/ubnt-wifi/connect", methods=["POST"])
def api_ubnt_wifi_connect():
    if not _exact_form(("profile",)):
        return api_error("UBNT connect requires one profile", 400)
    profile = request.form["profile"]
    if not profile or len(profile.encode("utf-8")) > 128 or any(
        ord(character) < 32 or ord(character) == 127 for character in profile
    ):
        return api_error("invalid UBNT profile", 400)
    return _start_ubnt_operation("connect", {"profile": profile})


@app.route("/api/ubnt-wifi/provision", methods=["POST"])
def api_ubnt_wifi_provision():
    fields = ("ssid", "security", "bssid", "password")
    if not _exact_form(fields):
        return api_error("new UBNT network requires SSID, security, BSSID, and password", 400)
    payload = {name: request.form[name] for name in fields}
    ssid = payload["ssid"]
    password = payload["password"]
    if (
        not ssid
        or len(ssid.encode("utf-8")) > 32
        or ssid.startswith(".")
        or "/" in ssid
        or any(ord(character) < 32 or ord(character) == 127 for character in ssid)
    ):
        payload["password"] = ""
        return api_error("SSID cannot be safely stored as a UBNT profile", 400)
    if payload["security"] not in ("wpa", "none"):
        payload["password"] = ""
        return api_error("only WPA/WPA2 Personal and open networks are supported", 400)
    if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", payload["bssid"]):
        payload["password"] = ""
        return api_error("invalid UBNT access-point address", 400)
    password_size = len(password.encode("utf-8"))
    password_has_control = any(
        ord(character) < 32 or ord(character) == 127 for character in password
    )
    if password_has_control or (
        payload["security"] == "wpa" and not 8 <= password_size <= 63
    ):
        payload["password"] = ""
        return api_error("WPA password must be 8 to 63 bytes without control characters", 400)
    if payload["security"] == "none" and password:
        payload["password"] = ""
        return api_error("open networks do not use a password", 400)
    response = _start_ubnt_operation("provision", payload)
    payload["password"] = ""
    return response


@app.route("/api/ubnt-wifi/resume", methods=["POST"])
def api_ubnt_wifi_resume():
    if request.form:
        return api_error("UBNT resume does not accept input", 400)
    return _start_ubnt_operation("resume")


@app.route("/api/speedtest", methods=["GET", "POST"])
def api_speedtest():
    if request.method == "POST":
        started = speedtest.start()
        message = "Speed test started" if started else "Speed test is already running"
        return jsonify({"ok": True, "message": message, "speedtest": speedtest.snapshot()})
    return jsonify({"ok": True, "speedtest": speedtest.snapshot()})


@app.route("/api/cop-alert", methods=["POST"])
def api_cop_alert():
    raw = request.values.get("active", "").strip().lower()
    if raw not in ("1", "0", "true", "false", "on", "off"):
        return api_error("active must be true or false", 400)
    active = raw in ("1", "true", "on")
    status = cop_alert.set_active(active)
    cop_led.notify()
    verb = "armed" if active else "disarmed"
    return jsonify({"ok": True, "message": f"COP ALERT {verb}", "cop_alert": status})


@app.route("/api/speakers")
def api_speakers():
    try:
        return jsonify(sonos.snapshot())
    except Exception as exc:
        return api_error(f"speaker discovery failed: {exc}", 503)


@app.route("/api/speakers/select", methods=["POST"])
def api_speaker_select():
    name = request.values.get("name", "").strip()
    if not name:
        return api_error("need name", 400)
    try:
        selected = sonos.select(name)
    except KeyError as exc:
        return api_error(exc.args[0], 404)
    except Exception as exc:
        return api_error(f"could not select Sonos group: {exc}", 502)
    return jsonify({"ok": True, "message": f"using {selected}", "device": selected})


@app.route("/api/speakers/group", methods=["POST"])
def api_speaker_group():
    name = request.values.get("name", "").strip()
    grouped = request.values.get("grouped", "").lower() in ("1", "true", "yes")
    try:
        message = sonos.group(name, grouped)
    except KeyError as exc:
        return api_error(exc.args[0], 404)
    except ValueError as exc:
        return api_error(exc, 400)
    except Exception as exc:
        return api_error(f"could not update Sonos group: {exc}", 502)
    return jsonify({"ok": True, "message": message})


@app.route("/api/speakers/volume", methods=["POST"])
def api_speaker_volume():
    name = request.values.get("name", "").strip()
    try:
        volume = int(request.values.get("volume", ""))
    except (TypeError, ValueError):
        return api_error("volume must be from 0 to 100", 400)
    try:
        volume = sonos.set_volume(name, volume)
    except KeyError as exc:
        return api_error(exc.args[0], 404)
    except Exception as exc:
        return api_error(f"could not set {name} volume: {exc}", 502)
    return jsonify({"ok": True, "message": f"{name} volume: {volume}", "volume": volume})


@app.route("/api/speakers/mute", methods=["POST"])
def api_speaker_mute():
    name = request.values.get("name", "").strip()
    try:
        muted = request_boolean("muted")
        muted = sonos.set_mute(name, muted)
    except ValueError as exc:
        return api_error(exc, 400)
    except KeyError as exc:
        return api_error(exc.args[0], 404)
    except Exception as exc:
        return api_error(f"could not mute {name}: {exc}", 502)
    verb = "muted" if muted else "unmuted"
    return jsonify({"ok": True, "message": f"{name} {verb}", "muted": muted})


@app.route("/api/speakers/group-volume", methods=["POST"])
def api_speaker_group_volume():
    try:
        volume = int(request.values.get("volume", ""))
    except (TypeError, ValueError):
        return api_error("volume must be from 0 to 100", 400)
    try:
        volume = sonos.set_group_volume(volume)
    except Exception as exc:
        return api_error(f"could not set Sonos group volume: {exc}", 502)
    return jsonify({"ok": True, "message": f"Group volume: {volume}", "volume": volume})


@app.route("/api/speakers/group-mute", methods=["POST"])
def api_speaker_group_mute():
    try:
        muted = request_boolean("muted")
        muted = sonos.set_group_mute(muted)
    except ValueError as exc:
        return api_error(exc, 400)
    except Exception as exc:
        return api_error(f"could not mute Sonos group: {exc}", 502)
    verb = "muted" if muted else "unmuted"
    return jsonify({"ok": True, "message": f"Sonos group {verb}", "muted": muted})


@app.route("/api/speakers/transport", methods=["POST"])
def api_speaker_transport():
    action = request.values.get("action", "").strip().lower()
    try:
        message = sonos.transport(action)
    except ValueError as exc:
        return api_error(exc, 400)
    except Exception as exc:
        return api_error(f"Sonos transport failed: {exc}", 502)
    return jsonify({"ok": True, "message": message})


@app.route("/api/speakers/art/<key>")
def api_speaker_art(key):
    if not re.fullmatch(r"[0-9a-f]{16}", key):
        return api_error("invalid Sonos album-art key", 400)
    try:
        content, content_type = sonos.album_art(key)
    except KeyError as exc:
        return api_error(exc.args[0], 404)
    except Exception as exc:
        return api_error(f"Sonos album art unavailable: {exc}", 502)
    response = app.response_class(content, mimetype=content_type)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


APP_ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" rx="112" fill="#19232d"/>
<path d="M106 178h300l35 74v142c0 17-13 30-30 30H101c-17 0-30-13-30-30V252l35-74Z" fill="#51b7c6"/>
<path d="M136 116h240l30 136H106l30-136Z" fill="#dbe9ee"/>
<path d="M165 141h182l17 86H148l17-86Z" fill="#22313d"/>
<circle cx="145" cy="385" r="42" fill="#111820"/><circle cx="367" cy="385" r="42" fill="#111820"/>
<path d="M216 303h80" stroke="#ef503f" stroke-width="30" stroke-linecap="round"/>
</svg>"""


@app.route("/manifest.webmanifest")
def manifest():
    response = jsonify(
        {
            "name": "Van Dashboard",
            "short_name": "Van",
            "id": "/",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#111820",
            "theme_color": "#111820",
            "icons": [{"src": "/app-icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        }
    )
    response.mimetype = "application/manifest+json"
    return response


@app.route("/app-icon.svg")
def app_icon():
    response = app.response_class(APP_ICON, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.route("/")
def index():
    return render_template("van_dashboard.html")


if __name__ == "__main__":
    cop_alert.start()
    cop_led.start()
    connectivity.start()
    starlink.start()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
