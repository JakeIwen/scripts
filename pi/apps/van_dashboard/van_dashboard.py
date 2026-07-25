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
import datetime
import glob
import hashlib
import json
import os
import plistlib
import re
import shlex
import socket
import struct
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from flask import Flask, jsonify, render_template, request

try:
    from pi.van_compute.scripts.van_compute_metrics import (
        ComputeMetricsError,
        ComputeMetricsReader,
    )
except ModuleNotFoundError:
    compute_scripts = os.environ.get(
        "VAN_COMPUTE_SCRIPTS", "/home/pi/van_compute/scripts"
    )
    if compute_scripts not in sys.path:
        sys.path.insert(0, compute_scripts)
    from van_compute_metrics import ComputeMetricsError, ComputeMetricsReader


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
USB_WATCH_TOOL = os.environ.get(
    "VAN_DASHBOARD_USB_WATCH_TOOL", "/home/pi/scripts/usb_watch.py"
)
USB2_RECOVERY_TOOL = os.environ.get(
    "VAN_DASHBOARD_USB2_RECOVERY_TOOL", "/home/pi/scripts/recover_usb2.sh"
)
CONNECTIVITY_INTERVAL = float(os.environ.get("VAN_DASHBOARD_CONNECTIVITY_INTERVAL", "30"))
OPENWRT_CLIENTS_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_OPENWRT_CLIENTS_TIMEOUT", "15")
)
SPEEDTEST_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_SPEEDTEST_TIMEOUT", "180"))
USB_WATCH_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_USB_WATCH_TIMEOUT", "10"))
UHUBCTL = os.environ.get("VAN_DASHBOARD_UHUBCTL", "/usr/sbin/uhubctl")
SUDO = os.environ.get("VAN_DASHBOARD_SUDO", "/usr/bin/sudo")
TEE = os.environ.get("VAN_DASHBOARD_TEE", "/usr/bin/tee")
USB_PORT_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_USB_PORT_TIMEOUT", "15"))
USB2_RECOVERY_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_USB2_RECOVERY_TIMEOUT", "30")
)
PRICE_CHECK_TOOL = os.environ.get(
    "VAN_DASHBOARD_PRICE_CHECK_TOOL", "/home/pi/scripts/price_check/main.py"
)
PRICE_CHECK_DB = os.path.expanduser(
    os.environ.get(
        "VAN_DASHBOARD_PRICE_CHECK_DB",
        "/home/pi/.local/share/price_check/price_check.sqlite3",
    )
)
PRICE_CHECK_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_PRICE_CHECK_TIMEOUT", "180"))
SYSTEM_MONITOR_TOOL = os.environ.get(
    "VAN_DASHBOARD_SYSTEM_MONITOR_TOOL", "/home/pi/scripts/system_event_monitor.py"
)
SYSTEM_MONITOR_DB = os.environ.get(
    "VAN_DASHBOARD_SYSTEM_MONITOR_DB", "/var/lib/vanpi-monitor/events.sqlite3"
)
SYSTEM_MONITOR_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_SYSTEM_MONITOR_TIMEOUT", "15")
)
COMPUTE_ROOT = os.environ.get(
    "VAN_DASHBOARD_COMPUTE_ROOT", "/home/pi/dev/obd-things/tmp/compute"
)
BACKUP_CONF = os.environ.get(
    "VAN_DASHBOARD_BACKUP_CONF", "/home/pi/scripts/backup/backup_conf.sh"
)
BACKUP_STAMP_DIR = os.environ.get(
    "VAN_DASHBOARD_BACKUP_STAMP_DIR", "/home/pi/backups/stamps"
)
BACKUP_CLONE_NOW = os.environ.get(
    "VAN_DASHBOARD_BACKUP_CLONE_NOW", "/home/pi/scripts/backup/clone_now.sh"
)
TIME_MACHINE_BUNDLE = os.environ.get(
    "VAN_DASHBOARD_TIME_MACHINE_BUNDLE", "/mnt/mbp2tbkup/m4mac.sparsebundle"
)
LSBLK = os.environ.get("VAN_DASHBOARD_LSBLK", "/usr/bin/lsblk")
BACKUP_STATUS_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_BACKUP_STATUS_TIMEOUT", "10"))
BACKUP_CLONE_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_BACKUP_CLONE_TIMEOUT", str(6 * 60 * 60 + 90))
)
IGNITIONMONCTL = os.environ.get(
    "VAN_DASHBOARD_IGNITIONMONCTL", "/home/pi/scripts/ignitionmonctl"
)
SYSTEMCTL = os.environ.get("VAN_DASHBOARD_SYSTEMCTL", "/usr/bin/systemctl")
IGNITIONMON_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_IGNITIONMON_TIMEOUT", "8"))
IGNITIONMON_MAX_MINUTES = 366 * 24 * 60
DISK_POLICY_CONF = os.environ.get(
    "VAN_DASHBOARD_DISK_POLICY_CONF", "/home/pi/scripts/disk_policy.sh"
)
DISKCTL = os.environ.get("VAN_DASHBOARD_DISKCTL", "/home/pi/scripts/diskctl")
DISK_EJECT_HOLD_DIR = os.environ.get(
    "VAN_DASHBOARD_DISK_EJECT_HOLD_DIR", "/run/lock/vanpi-disk-eject"
)
DISK_STATUS_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_DISK_STATUS_TIMEOUT", "10"))
DISK_ACTION_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_DISK_ACTION_TIMEOUT", "150"))
SAFE_REBOOT = os.environ.get(
    "VAN_DASHBOARD_SAFE_REBOOT", "/home/pi/scripts/safe_reboot.sh"
)
SAFE_POWER_DOWN = os.environ.get(
    "VAN_DASHBOARD_SAFE_POWER_DOWN", "/home/pi/scripts/safe_power_down.sh"
)
SYSTEM_POWER_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_SYSTEM_POWER_TIMEOUT", "180")
)
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
LIGHT_POWER_SWITCHES = {
    "exterior": ("switch.ext_flood", "Exterior power"),
    "solder": ("switch.solder_flood", "Solder power"),
}
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


def read_text_file(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read().strip()


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
        self.switch_entities = {
            entity for entity, _label in LIGHT_POWER_SWITCHES.values()
        }
        self.targets = {"all": ordered_entities}
        self.targets.update(
            {
                f"group:{group_id}": tuple(entity for entity, _label in lights)
                for group_id, _group_label, lights in LIGHT_GROUPS
            }
        )
        self.targets.update({entity: (entity,) for entity in self.entities})
        self.targets.update({entity: (entity,) for entity in self.switch_entities})

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
            if not isinstance(entity, str) or not (
                re.fullmatch(r"light\.[a-z0-9_]+", entity)
                or entity in {
                    switch_entity
                    for switch_entity, _label in LIGHT_POWER_SWITCHES.values()
                }
            ):
                raise LightingCommandError("Home Assistant returned an invalid lighting entity")
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
            switch_config = LIGHT_POWER_SWITCHES.get(group_id)
            power_switch = None
            if switch_config is not None:
                switch_entity, switch_label = switch_config
                switch_value = observed.get(
                    switch_entity, {"state": "unknown", "brightness": None}
                )
                power_switch = {
                    "entity_id": switch_entity,
                    "label": switch_label,
                    "state": switch_value["state"],
                    "available": switch_value["state"] in ("on", "off"),
                }
            groups.append(
                {
                    "id": group_id,
                    "label": group_label,
                    "state": self.aggregate(lights),
                    "lights": lights,
                    "power_switch": power_switch,
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


class OpenWrtClientsError(RuntimeError):
    pass


class OpenWrtClientsController:
    """Read the fixed, passive OpenWrt client inventory on demand."""

    CLIENT_FIELDS = {
        "name",
        "hostname_known",
        "ip",
        "mac",
        "connection",
        "interface",
        "neighbor_state",
        "signal_dbm",
        "rx_rate_bps",
        "tx_rate_bps",
        "rx_bytes",
        "tx_bytes",
        "lease_expires_at",
    }
    NUMBER_FIELDS = {
        "rx_rate_bps",
        "tx_rate_bps",
        "rx_bytes",
        "tx_bytes",
        "lease_expires_at",
    }

    def __init__(
        self,
        collector=CONNECTIVITY_STATUS,
        command=run_command,
        timeout=OPENWRT_CLIENTS_TIMEOUT,
    ):
        self.collector = collector
        self.command = command
        self.timeout = timeout

    @staticmethod
    def _valid_ipv4(value):
        if value is None:
            return True
        if not isinstance(value, str) or not re.fullmatch(
            r"(?:\d{1,3}\.){3}\d{1,3}", value
        ):
            return False
        return all(0 <= int(part) <= 255 for part in value.split("."))

    @classmethod
    def parse_status(cls, output):
        try:
            payload = json.loads(output)
        except (TypeError, ValueError) as exc:
            raise OpenWrtClientsError("client collector returned invalid JSON") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or not isinstance(payload.get("checked_at"), int)
            or isinstance(payload.get("checked_at"), bool)
            or not isinstance(payload.get("clients"), list)
            or len(payload["clients"]) > 256
        ):
            raise OpenWrtClientsError("client collector returned an invalid payload")

        clients = []
        for item in payload["clients"]:
            if not isinstance(item, dict) or set(item) != cls.CLIENT_FIELDS:
                raise OpenWrtClientsError("client collector returned an invalid device")
            if (
                not isinstance(item["name"], str)
                or not item["name"]
                or len(item["name"]) > 100
                or not isinstance(item["hostname_known"], bool)
                or not cls._valid_ipv4(item["ip"])
                or not isinstance(item["mac"], str)
                or not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", item["mac"])
                or item["connection"] not in ("wifi", "lan")
                or not isinstance(item["interface"], str)
                or len(item["interface"]) > 50
                or (
                    item["neighbor_state"] is not None
                    and item["neighbor_state"]
                    not in ("REACHABLE", "DELAY", "PROBE", "PERMANENT", "STALE")
                )
                or (
                    item["signal_dbm"] is not None
                    and (
                        not isinstance(item["signal_dbm"], int)
                        or not -150 <= item["signal_dbm"] <= 0
                    )
                )
                or any(
                    item[field] is not None
                    and (
                        not isinstance(item[field], int)
                        or isinstance(item[field], bool)
                        or item[field] < 0
                    )
                    for field in cls.NUMBER_FIELDS
                )
            ):
                raise OpenWrtClientsError("client collector returned invalid device data")
            clients.append(item)

        wifi_count = sum(item["connection"] == "wifi" for item in clients)
        lan_count = len(clients) - wifi_count
        invalid_counts = any(
            not isinstance(payload.get(field), int)
            or isinstance(payload.get(field), bool)
            for field in ("client_count", "wifi_count", "lan_count")
        )
        if (
            invalid_counts
            or payload.get("client_count") != len(clients)
            or payload.get("wifi_count") != wifi_count
            or payload.get("lan_count") != lan_count
        ):
            raise OpenWrtClientsError("client collector returned inconsistent counts")
        return {
            "version": 1,
            "checked_at": payload["checked_at"],
            "client_count": len(clients),
            "wifi_count": wifi_count,
            "lan_count": lan_count,
            "clients": clients,
        }

    def status(self):
        try:
            result = self.command([self.collector, "--clients"], timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise OpenWrtClientsError(
                f"client query timed out after {self.timeout:g} seconds"
            ) from exc
        except OSError as exc:
            raise OpenWrtClientsError(f"could not start client query: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or "client query failed").strip()
            raise OpenWrtClientsError(detail[-300:])
        return self.parse_status(result.stdout)


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


class UsbDeviceMonitor:
    """Track live USB devices using usb_watch.py's one-shot collector."""

    DEVICE_FIELDS = {
        "bus",
        "device_id",
        "description",
        "present_count",
        "labels",
        "root_hub",
    }
    INSTANCE_FIELDS = {
        "device_number",
        "location",
        "parent_location",
        "port",
        "labels",
    }

    def __init__(
        self,
        tool=USB_WATCH_TOOL,
        command=run_command,
        timeout=USB_WATCH_TIMEOUT,
        wall_clock=time.time,
    ):
        self.tool = tool
        self.command = command
        self.timeout = timeout
        self.wall_clock = wall_clock
        self.operation_lock = threading.Lock()
        self.lock = threading.Lock()
        self.seen = {}
        self.baseline = True
        self.checked_at = None
        self.last_success_at = None
        self.last_error = None

    @classmethod
    def parse_current(cls, output):
        try:
            payload = json.loads(output)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"usb_watch returned invalid JSON: {exc}") from exc
        if not isinstance(payload, dict) or set(payload) != {"version", "devices"}:
            raise ValueError("usb_watch returned an unexpected schema")
        if payload["version"] not in (1, 2) or type(payload["version"]) is not int:
            raise ValueError("usb_watch returned an unsupported version")
        if not isinstance(payload["devices"], list):
            raise ValueError("usb_watch devices were not a list")
        parsed = {}
        for device in payload["devices"]:
            expected_fields = cls.DEVICE_FIELDS | ({"instances"} if payload["version"] == 2 else set())
            if not isinstance(device, dict) or set(device) != expected_fields:
                raise ValueError("usb_watch returned an invalid device")
            bus = device["bus"]
            device_id = device["device_id"]
            description = device["description"]
            count = device["present_count"]
            labels = device["labels"]
            root_hub = device["root_hub"]
            instances = device.get("instances", [])
            if not isinstance(bus, str) or not re.fullmatch(r"\d{3}", bus):
                raise ValueError("usb_watch returned an invalid bus")
            if not isinstance(device_id, str) or not re.fullmatch(
                r"(?:[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}|unknown)", device_id
            ):
                raise ValueError("usb_watch returned an invalid device ID")
            if not isinstance(description, str) or not description or len(description) > 500:
                raise ValueError("usb_watch returned an invalid description")
            if type(count) is not int or count < 1:
                raise ValueError("usb_watch returned an invalid device count")
            if (
                not isinstance(labels, list)
                or any(not isinstance(label, str) or not label for label in labels)
                or len(labels) != len(set(labels))
            ):
                raise ValueError("usb_watch returned invalid filesystem labels")
            if type(root_hub) is not bool:
                raise ValueError("usb_watch returned an invalid root-hub flag")
            if not isinstance(instances, list):
                raise ValueError("usb_watch returned invalid topology instances")
            parsed_instances = []
            for instance in instances:
                if not isinstance(instance, dict) or set(instance) != cls.INSTANCE_FIELDS:
                    raise ValueError("usb_watch returned an invalid topology instance")
                if (
                    type(instance["device_number"]) is not int
                    or instance["device_number"] < 1
                    or not isinstance(instance["location"], str)
                    or not re.fullmatch(r"\d+-\d+(?:\.\d+)*", instance["location"])
                    or not isinstance(instance["parent_location"], str)
                    or not re.fullmatch(r"\d+(?:-\d+(?:\.\d+)*)?", instance["parent_location"])
                    or type(instance["port"]) is not int
                    or instance["port"] < 1
                    or not isinstance(instance["labels"], list)
                    or any(not isinstance(label, str) or not label for label in instance["labels"])
                ):
                    raise ValueError("usb_watch returned an invalid topology instance")
                parsed_instances.append(copy.deepcopy(instance))
            key = (bus, device_id, description)
            if key in parsed:
                raise ValueError("usb_watch returned a duplicate device")
            parsed[key] = {
                "bus": bus,
                "device_id": device_id.lower(),
                "description": description,
                "present_count": count,
                "labels": list(labels),
                "root_hub": root_hub,
                "instances": parsed_instances,
            }
        return parsed

    def refresh(self):
        with self.operation_lock:
            now = int(self.wall_clock())
            try:
                result = self.command(
                    [sys.executable, self.tool, "--json"], timeout=self.timeout
                )
                if result.returncode:
                    detail = (result.stderr or result.stdout or "usb_watch failed").strip()
                    raise RuntimeError(detail[-500:])
                current = self.parse_current(result.stdout)
            except subprocess.TimeoutExpired:
                error = f"usb_watch timed out after {self.timeout:g} seconds"
            except (OSError, RuntimeError, ValueError) as exc:
                error = str(exc)
            else:
                error = None

            with self.lock:
                self.checked_at = now
                if error:
                    self.last_error = error
                    return self._snapshot_unlocked()

                for key, device in current.items():
                    count = device["present_count"]
                    if key not in self.seen:
                        event = None if self.baseline else {"kind": "plugged", "at": now}
                        self.seen[key] = {
                            **device,
                            "max_count": count,
                            "event": event,
                        }
                        continue
                    seen = self.seen[key]
                    if seen["present_count"] == 0:
                        seen["event"] = {"kind": "replugged", "at": now}
                    seen["present_count"] = count
                    seen["max_count"] = max(seen["max_count"], count)
                    if device["labels"]:
                        seen["labels"] = device["labels"]
                    seen["instances"] = device["instances"]

                for key, seen in self.seen.items():
                    if key not in current and seen["present_count"] > 0:
                        seen["present_count"] = 0
                        seen["event"] = {"kind": "unplugged", "at": now}

                self.baseline = False
                self.last_success_at = now
                self.last_error = None
                return self._snapshot_unlocked()

    def snapshot(self):
        with self.lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self):
        devices = []
        for seen in sorted(
            self.seen.values(),
            key=lambda item: (
                item["bus"],
                0 if item["root_hub"] else 1,
                item["description"].lower(),
            ),
        ):
            if seen["present_count"] == 0:
                status = "unplugged"
            elif seen["present_count"] < seen["max_count"]:
                status = "partial"
            elif seen["root_hub"]:
                status = "root"
            else:
                status = "present"
            devices.append({**seen, "status": status})

        physical = [device for device in devices if not device["root_hub"]]
        present = [device for device in physical if device["present_count"] > 0]
        labels = sorted(
            {
                label
                for device in present
                for label in device["labels"]
            }
        )
        return {
            "checked_at": self.checked_at,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "present_device_count": sum(device["present_count"] for device in present),
            "unplugged_device_count": sum(
                device["max_count"] for device in physical if device["present_count"] == 0
            ),
            "storage_labels": labels,
            "devices": devices,
        }


UHUB_HUB_RE = re.compile(r"^Current status for hub (\S+) \[(.+)\]$")
UHUB_PORT_RE = re.compile(r"^\s+Port (\d+):\s+(.+?)(?:\s+\[([0-9A-Fa-f]{4}:[0-9A-Fa-f]{4})\s+(.+)\])?$")
USB_PORT_KEY_RE = re.compile(r"^(\d+(?:-\d+(?:\.\d+)*)?):([1-9]\d*)$")


def parse_uhubctl_status(output):
    """Parse the stable human-readable status emitted by uhubctl 2.x."""
    hubs = {}
    current = None
    for raw_line in str(output or "").splitlines():
        hub_match = UHUB_HUB_RE.match(raw_line.strip())
        if hub_match:
            location, details = hub_match.groups()
            port_match = re.search(r",\s*(\d+) ports?,\s*([^,\]]+)\s*$", details)
            identity = details.split(", USB ", 1)[0]
            parts = identity.split(None, 1)
            current = {
                "location": location,
                "device_id": parts[0].lower() if parts else "unknown",
                "description": parts[1] if len(parts) > 1 else "USB hub",
                "port_count": int(port_match.group(1)) if port_match else 0,
                "switching": port_match.group(2).strip() if port_match else "unknown",
                "ports": {},
            }
            hubs[location] = current
            continue
        port_match = UHUB_PORT_RE.match(raw_line)
        if current is None or not port_match:
            continue
        port, status, device_id, description = port_match.groups()
        current["ports"][int(port)] = {
            "powered": "power" in status.split(),
            "status": status,
            "device_id": device_id.lower() if device_id else None,
            "description": description if description else None,
        }
    return hubs


class UsbPortController:
    """Discover USB hub ports and run only previously discovered fixed actions."""

    ACTIONS = {"off", "on", "cycle"}

    def __init__(
        self,
        device_monitor,
        command=run_command,
        recovery_tool=USB2_RECOVERY_TOOL,
        sys_root="/sys",
        dev_root="/dev",
        mounts_path="/proc/self/mounts",
        timeout=USB_PORT_TIMEOUT,
        recovery_timeout=USB2_RECOVERY_TIMEOUT,
        wall_clock=time.time,
        sleeper=time.sleep,
    ):
        self.device_monitor = device_monitor
        self.command = command
        self.recovery_tool = recovery_tool
        self.sys_root = sys_root
        self.dev_root = dev_root
        self.mounts_path = mounts_path
        self.timeout = timeout
        self.recovery_timeout = recovery_timeout
        self.wall_clock = wall_clock
        self.sleeper = sleeper
        self.lock = threading.RLock()
        self.targets = {}
        self.data = {
            "checked_at": None,
            "last_error": None,
            "hubs": [],
            "operation": {"status": "idle"},
        }

    def _kernel_ports(self):
        ports = {}
        pattern = os.path.join(
            self.sys_root, "bus", "usb", "devices", "*", "*-port*", "disable"
        )
        for path in glob.glob(pattern):
            port_dir = os.path.basename(os.path.dirname(path))
            interface = os.path.basename(os.path.dirname(os.path.dirname(path)))
            match = re.fullmatch(r"(.+)-port(\d+)", port_dir)
            if not match or not interface.endswith(":1.0"):
                continue
            interface_location = interface.removesuffix(":1.0")
            location = (
                interface_location[:-2]
                if interface_location.endswith("-0")
                else interface_location
            )
            if not re.fullmatch(r"\d+(?:-\d+(?:\.\d+)*)?", location):
                continue
            port = int(match.group(2))
            try:
                disabled = read_text_file(path) == "1"
            except OSError:
                continue
            ports[(location, port)] = {
                "disable_path": path,
                "enabled": not disabled,
            }
        return ports

    def _mounted_sources(self):
        sources = set()
        try:
            with open(self.mounts_path, encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
        except OSError:
            return sources
        for line in lines:
            fields = line.split()
            if fields and (
                fields[0].startswith("/dev/")
                or fields[0].startswith(self.dev_root.rstrip(os.sep) + os.sep)
            ):
                sources.add(os.path.realpath(fields[0].replace("\\040", " ")))
        return sources

    def _mounted_labels(self, labels):
        sources = self._mounted_sources()
        mounted = []
        for label in labels:
            path = os.path.join(self.dev_root, "disk", "by-label", label)
            if os.path.exists(path) and os.path.realpath(path) in sources:
                mounted.append(label)
        return sorted(mounted)

    @staticmethod
    def _instances(usb_state):
        result = []
        for device in usb_state.get("devices", ()):
            if device.get("present_count", 0) < 1:
                continue
            for instance in device.get("instances", ()):
                result.append(
                    {
                        **instance,
                        "description": device.get("description") or "USB device",
                        "device_id": device.get("device_id"),
                        "root_hub": bool(device.get("root_hub")),
                    }
                )
        return result

    @staticmethod
    def _child_location(location, port):
        return f"{location}-{port}" if location.isdigit() else f"{location}.{port}"

    @staticmethod
    def _companion_route(location):
        """Return the Pi 4 physical-port route for USB 2/USB 3 hub companions."""
        usb2 = re.fullmatch(r"1-1\.(\d+(?:\.\d+)*)", location)
        if usb2:
            return "usb2", usb2.group(1)
        usb3 = re.fullmatch(r"2-(\d+(?:\.\d+)*)", location)
        if usb3:
            return "usb3", usb3.group(1)
        return None

    @classmethod
    def _presentation_hubs(cls, hubs, targets):
        """Merge dual USB 2/USB 3 hub trees into user-facing physical ports.

        A Raspberry Pi 4 inserts its VIA USB 2 hub at ``1-1`` while USB 3 is
        rooted directly at bus 2. Matching routes below those points are the
        two logical sides of the same physical hub. Chained hub-controller
        uplinks are omitted and their external ports are flattened in route
        order.
        """
        companions = {"usb2": {}, "usb3": {}}
        for hub in hubs:
            route = cls._companion_route(hub["location"])
            if route and hub["method"] == "power":
                side, path = route
                companions[side][path] = hub

        paired_routes = set(companions["usb2"]) & set(companions["usb3"])
        paired_routes = {
            route
            for route in paired_routes
            if {
                port["port"] for port in companions["usb2"][route]["ports"]
            }
            == {
                port["port"] for port in companions["usb3"][route]["ports"]
            }
        }
        consumed_locations = {
            companions[side][route]["location"]
            for route in paired_routes
            for side in ("usb2", "usb3")
        }
        roots = sorted(
            (
                route
                for route in paired_routes
                if "." not in route or route.rsplit(".", 1)[0] not in paired_routes
            ),
            key=lambda route: [int(part) for part in route.split(".")],
        )

        presented = []
        for root in roots:
            physical_ports = []

            def append_route(route):
                usb2_hub = companions["usb2"][route]
                usb3_hub = companions["usb3"][route]
                usb2_ports = {port["port"]: port for port in usb2_hub["ports"]}
                usb3_ports = {port["port"]: port for port in usb3_hub["ports"]}
                for topology_port in sorted(usb2_ports):
                    child_route = f"{route}.{topology_port}"
                    if child_route in paired_routes:
                        append_route(child_route)
                        continue
                    halves = [usb2_ports[topology_port], usb3_ports[topology_port]]
                    primary = usb3_ports[topology_port]
                    enabled_values = [
                        port["enabled"] for port in halves if port["enabled"] is not None
                    ]
                    descriptions = sorted(
                        {
                            description
                            for port in halves
                            for description in port["device_descriptions"]
                        }
                    )
                    storage_labels = sorted(
                        {
                            label
                            for port in halves
                            for label in port["storage_labels"]
                        }
                    )
                    mounted_labels = sorted(
                        {
                            label
                            for port in halves
                            for label in port["mounted_labels"]
                        }
                    )
                    physical_port = len(physical_ports) + 1
                    merged = {
                        **primary,
                        "port": physical_port,
                        "enabled": all(enabled_values) if enabled_values else None,
                        "device_descriptions": descriptions,
                        "downstream_device_count": sum(
                            port["downstream_device_count"] for port in halves
                        ),
                        "storage_labels": storage_labels,
                        "mounted_labels": mounted_labels,
                        "topology_locations": [
                            f"{port['location']}:{topology_port}" for port in halves
                        ],
                    }
                    physical_ports.append(merged)

                    # Keep the actual USB 3 location/port for uhubctl. Its
                    # default duality handling also switches the USB 2 side.
                    target = targets[primary["key"]]
                    target.update(
                        {
                            "enabled": merged["enabled"],
                            "device_descriptions": descriptions,
                            "downstream_device_count": merged[
                                "downstream_device_count"
                            ],
                            "storage_labels": storage_labels,
                            "mounted_labels": mounted_labels,
                        }
                    )

            append_route(root)
            root_hub = companions["usb3"][root]
            presented.append(
                {
                    "location": root_hub["location"],
                    "description": "External USB hub",
                    "detail": (
                        f"{len(physical_ports)} physical ports · paired USB 2/USB 3"
                    ),
                    "method": "power",
                    "physical": True,
                    "advanced": False,
                    "ports": physical_ports,
                }
            )

        for hub in hubs:
            if hub["location"] in consumed_locations:
                continue
            presented.append(
                {
                    **hub,
                    "physical": False,
                    "advanced": hub["location"] in {"1", "2", "1-1"},
                }
            )
        return presented

    def refresh(self, usb_state=None):
        usb_state = usb_state or self.device_monitor.refresh()
        error = None
        smart_hubs = {}
        try:
            result = self.command([SUDO, "-n", UHUBCTL], timeout=self.timeout)
            if result.returncode:
                detail = (result.stderr or result.stdout or "uhubctl failed").strip()
                raise RuntimeError(detail[-500:])
            smart_hubs = parse_uhubctl_status(result.stdout)
        except subprocess.TimeoutExpired:
            error = f"uhubctl status timed out after {self.timeout:g} seconds"
        except (OSError, RuntimeError, ValueError) as exc:
            error = str(exc)

        kernel_ports = self._kernel_ports()
        instances = self._instances(usb_state)
        keys = set(kernel_ports)
        for location, hub in smart_hubs.items():
            keys.update((location, port) for port in hub["ports"])
        hubs = {}
        targets = {}
        for location, port in sorted(
            keys,
            key=lambda item: (
                [int(value) for value in re.split(r"[-.]", item[0])],
                item[1],
            ),
        ):
            smart_hub = smart_hubs.get(location)
            smart_port = smart_hub.get("ports", {}).get(port) if smart_hub else None
            kernel_port = kernel_ports.get((location, port))
            if smart_hub:
                method = "power"
                enabled = (
                    kernel_port["enabled"]
                    if kernel_port is not None
                    else bool(smart_port and smart_port["powered"])
                )
                hub_description = smart_hub["description"]
            else:
                method = "disable"
                enabled = kernel_port["enabled"] if kernel_port is not None else None
                hub_device = next(
                    (item for item in instances if item["location"] == location), None
                )
                hub_description = (
                    hub_device["description"]
                    if hub_device
                    else f"USB {location} hub"
                )
            child = self._child_location(location, port)
            direct = [item for item in instances if item["location"] == child]
            downstream = [
                item
                for item in instances
                if item["location"] == child or item["location"].startswith(child + ".")
            ]
            descriptions = sorted({item["description"] for item in direct})
            labels = sorted(
                {label for item in downstream for label in item.get("labels", ())}
            )
            mounted_labels = self._mounted_labels(labels)
            key = f"{location}:{port}"
            public = {
                "key": key,
                "location": location,
                "port": port,
                "method": method,
                "enabled": enabled,
                "device_descriptions": descriptions,
                "downstream_device_count": len(downstream),
                "storage_labels": labels,
                "mounted_labels": mounted_labels,
            }
            hubs.setdefault(
                location,
                {
                    "location": location,
                    "description": hub_description,
                    "method": method,
                    "ports": [],
                },
            )["ports"].append(public)
            targets[key] = {
                **public,
                "disable_path": kernel_port.get("disable_path") if kernel_port else None,
            }

        presented_hubs = self._presentation_hubs(list(hubs.values()), targets)
        now = int(self.wall_clock())
        with self.lock:
            self.targets = targets
            self.data.update(
                {
                    "checked_at": now,
                    "last_error": error,
                    "hubs": presented_hubs,
                }
            )
            return copy.deepcopy(self.data)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def start_action(self, key, action):
        if not isinstance(key, str) or not USB_PORT_KEY_RE.fullmatch(key):
            raise ValueError("unknown USB port")
        if action not in self.ACTIONS:
            raise ValueError("USB port action must be on, off, or cycle")
        self.refresh()
        with self.lock:
            if self.data["operation"].get("status") == "running":
                raise RuntimeError("another USB port action is already running")
            target = self.targets.get(key)
            if target is None:
                raise ValueError("unknown USB port")
            if action in ("off", "cycle") and target["mounted_labels"]:
                labels = ", ".join(target["mounted_labels"])
                raise RuntimeError(
                    f"refusing to disconnect mounted storage ({labels}); unmount it first"
                )
            started_at = int(self.wall_clock())
            self.data["operation"] = {
                "status": "running",
                "key": key,
                "action": action,
                "started_at": started_at,
                "completed_at": None,
                "error": None,
            }
            thread = threading.Thread(
                target=self._run_action,
                args=(copy.deepcopy(target), action, started_at),
                name="usb-port-action",
                daemon=True,
            )
            thread.start()
            return copy.deepcopy(self.data)

    def start_recovery(self):
        """Start the fixed, guarded Raspberry Pi internal USB 2 hub recovery."""
        self.refresh()
        with self.lock:
            if self.data["operation"].get("status") == "running":
                raise RuntimeError("another USB port action is already running")
            started_at = int(self.wall_clock())
            self.data["operation"] = {
                "status": "running",
                "key": "Pi internal USB 2 hub",
                "action": "restore",
                "started_at": started_at,
                "completed_at": None,
                "message": None,
                "error": None,
            }
            thread = threading.Thread(
                target=self._run_recovery,
                args=(started_at,),
                name="usb2-recovery",
                daemon=True,
            )
            thread.start()
            return copy.deepcopy(self.data)

    def _command_ok(self, args, input_text=None):
        result = self.command(args, timeout=self.timeout, input_text=input_text)
        if result.returncode:
            detail = (result.stderr or result.stdout or "USB port command failed").strip()
            raise RuntimeError(detail[-500:])

    def _run_action(self, target, action, started_at):
        error = None
        try:
            if target["method"] == "power":
                self._command_ok(
                    [
                        SUDO,
                        "-n",
                        UHUBCTL,
                        "-l",
                        target["location"],
                        "-p",
                        str(target["port"]),
                        "-a",
                        action,
                    ]
                )
            else:
                path = target.get("disable_path")
                expected_root = os.path.join(self.sys_root, "bus", "usb", "devices")
                if not path or not path.startswith(expected_root + os.sep):
                    raise RuntimeError("kernel USB port control disappeared")
                values = ("1\n", "0\n") if action == "cycle" else (("1\n",) if action == "off" else ("0\n",))
                for index, value in enumerate(values):
                    self._command_ok(
                        [SUDO, "-n", TEE, path],
                        input_text=value,
                    )
                    if action == "cycle" and index == 0:
                        self.sleeper(2)
            self.sleeper(1)
            usb_state = self.device_monitor.refresh()
            self.refresh(usb_state)
        except subprocess.TimeoutExpired:
            error = f"USB port action timed out after {self.timeout:g} seconds"
        except (OSError, RuntimeError, ValueError) as exc:
            error = str(exc)
        with self.lock:
            self.data["operation"] = {
                "status": "error" if error else "complete",
                "key": target["key"],
                "action": action,
                "started_at": started_at,
                "completed_at": int(self.wall_clock()),
                "error": error,
            }

    def _run_recovery(self, started_at):
        error = None
        message = None
        try:
            result = self.command(
                [self.recovery_tool], timeout=self.recovery_timeout
            )
            if result.returncode:
                detail = (result.stderr or result.stdout or "USB 2 recovery failed").strip()
                raise RuntimeError(detail[-500:])
            lines = (result.stdout or "").strip().splitlines()
            message = lines[-1][-500:] if lines else "USB 2 hub restored"
            usb_state = self.device_monitor.refresh()
            self.refresh(usb_state)
        except subprocess.TimeoutExpired:
            error = f"USB 2 recovery timed out after {self.recovery_timeout:g} seconds"
        except (OSError, RuntimeError, ValueError) as exc:
            error = str(exc)
        with self.lock:
            self.data["operation"] = {
                "status": "error" if error else "complete",
                "key": "Pi internal USB 2 hub",
                "action": "restore",
                "started_at": started_at,
                "completed_at": int(self.wall_clock()),
                "message": message,
                "error": error,
            }


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


class PriceCheckCommandError(RuntimeError):
    pass


class PriceCheckController:
    """Use the price-check CLI as the sole database and validation boundary."""

    def __init__(
        self,
        tool=PRICE_CHECK_TOOL,
        database=PRICE_CHECK_DB,
        command=run_command,
        timeout=PRICE_CHECK_TIMEOUT,
    ):
        self.tool = tool
        self.database = database
        self.command = command
        self.timeout = timeout

    def _run(self, *arguments, timeout=None):
        argv = [
            sys.executable,
            self.tool,
            "--db",
            self.database,
            "--json",
            *[str(argument) for argument in arguments],
        ]
        try:
            result = self.command(argv, timeout=timeout or self.timeout)
        except subprocess.TimeoutExpired as error:
            raise PriceCheckCommandError("price check timed out") from error
        except OSError as error:
            raise PriceCheckCommandError(f"could not start price checker: {error}") from error
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError) as error:
            detail = (result.stderr or result.stdout or "no output").strip()[-500:]
            raise PriceCheckCommandError(
                f"price checker returned invalid output: {detail}"
            ) from error
        if result.returncode or not isinstance(payload, dict) or payload.get("ok") is not True:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise PriceCheckCommandError(message or "price checker failed")
        return payload

    def status(self):
        return self._run("list", timeout=20)

    def add(self, parser, threshold, url, title=""):
        return self._run("add", parser, threshold, url, title, timeout=20)

    def edit(self, item_id, parser, threshold, url, title=""):
        return self._run(
            "edit", item_id, parser, threshold, url, title, timeout=20
        )

    def mute(self, item_id, days):
        return self._run("mute", item_id, days, timeout=20)

    def schedule(self):
        return self._run("schedule", timeout=25)

    def parse_schedule(self, expression):
        return self._run("schedule-parse", expression, timeout=25)

    def set_schedule(self, expression):
        return self._run("schedule-set", expression, timeout=30)

    def remove(self, item_id):
        return self._run("remove", item_id, timeout=20)

    def check(self, target="all"):
        return self._run("check", target)


class SystemMonitorCommandError(RuntimeError):
    pass


class SystemMonitorClient:
    """Read the passive system monitor through bounded, fixed CLI commands."""

    def __init__(
        self,
        tool=SYSTEM_MONITOR_TOOL,
        database=SYSTEM_MONITOR_DB,
        command=run_command,
        timeout=SYSTEM_MONITOR_TIMEOUT,
    ):
        self.tool = tool
        self.database = database
        self.command = command
        self.timeout = timeout

    def _run_json(self, command_args):
        args = [
            sys.executable,
            self.tool,
            "--database",
            self.database,
            *command_args,
        ]
        try:
            result = self.command(args, timeout=self.timeout)
        except subprocess.TimeoutExpired as error:
            raise SystemMonitorCommandError(
                f"system monitor command timed out after {self.timeout:g} seconds"
            ) from error
        except OSError as error:
            raise SystemMonitorCommandError(str(error)) from error
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError) as error:
            detail = (result.stderr or result.stdout or "no output").strip()[-500:]
            raise SystemMonitorCommandError(
                f"system monitor returned invalid output: {detail}"
            ) from error
        if result.returncode or not isinstance(payload, dict) or payload.get("ok") is not True:
            message = payload.get("message") if isinstance(payload, dict) else None
            detail = (result.stderr or "").strip()[-500:]
            raise SystemMonitorCommandError(message or detail or "system monitor command failed")
        return payload

    def report(self, hours=6):
        return self._run_json(
            [
                "report",
                "--hours",
                str(int(hours)),
                "--limit",
                "100",
                "--json",
            ]
        )

    def crash_analysis(self):
        return self._run_json(["crash-report", "--save", "--json"])

    def crash_history(self, limit=20):
        return self._run_json(
            [
                "crash-history",
                "--limit",
                str(max(1, min(int(limit), 100))),
                "--full",
                "--json",
            ]
        )


class BackupStatusError(RuntimeError):
    pass


class BackupManager:
    """Read backup evidence and serialize safe hotspare clone requests.

    Configuration is parsed as data rather than sourced, so inspecting status
    cannot execute backup_conf.sh or load its secrets file. Clone targets are
    restricted to the labels already present in CLONE_TARGETS; the dashboard
    never accepts a block-device path and never exposes
    /home/pi/scripts/backup/clone_to_sd.sh --init.
    """

    CLONE_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

    def __init__(
        self,
        config=BACKUP_CONF,
        stamp_dir=BACKUP_STAMP_DIR,
        clone_tool=BACKUP_CLONE_NOW,
        time_machine_bundle=TIME_MACHINE_BUNDLE,
        command=run_command,
        timeout=BACKUP_STATUS_TIMEOUT,
        clone_timeout=BACKUP_CLONE_TIMEOUT,
        wall_clock=time.time,
    ):
        self.config = config
        self.stamp_dir = stamp_dir
        self.clone_tool = clone_tool
        self.time_machine_bundle = time_machine_bundle
        self.command = command
        self.timeout = timeout
        self.clone_timeout = clone_timeout
        self.wall_clock = wall_clock
        self.lock = threading.Lock()
        self.thread = None
        self.operation = {
            "status": "idle",
            "target": None,
            "started_at": None,
            "completed_at": None,
            "error": None,
        }

    @classmethod
    def parse_config(cls, text):
        match = re.search(r"^\s*CLONE_TARGETS=\(([^)]*)\)\s*$", text, re.MULTILINE)
        if not match:
            raise BackupStatusError("backup configuration has no CLONE_TARGETS")
        try:
            entries = shlex.split(match.group(1), comments=True, posix=True)
        except ValueError as exc:
            raise BackupStatusError(f"could not parse CLONE_TARGETS: {exc}") from exc
        targets = []
        seen = set()
        for entry in entries:
            label, separator, raw_interval = entry.rpartition(":")
            if (
                not separator
                or not cls.CLONE_TARGET_RE.fullmatch(label)
                or not raw_interval.isdigit()
                or not 1 <= int(raw_interval) <= 3650
                or label in seen
            ):
                raise BackupStatusError("backup configuration has an invalid clone target")
            seen.add(label)
            targets.append({"label": label, "interval_days": int(raw_interval)})
        if not targets:
            raise BackupStatusError("backup configuration has no clone targets")

        def integer(name, default):
            value = re.search(rf"^\s*{re.escape(name)}=(\d+)\s*(?:#.*)?$", text, re.MULTILINE)
            return int(value.group(1)) if value else default

        return {
            "targets": targets,
            "borg_stale_hours": integer("BORG_STALE_HOURS", 48),
            "clone_stale_factor": integer("CLONE_STALE_FACTOR", 2),
        }

    def _configuration(self):
        try:
            return self.parse_config(read_text_file(self.config))
        except OSError as exc:
            raise BackupStatusError(f"could not read backup configuration: {exc}") from exc

    @staticmethod
    def _flatten_block_devices(devices, parent=None):
        rows = []
        for device in devices if isinstance(devices, list) else ():
            if not isinstance(device, dict):
                continue
            row = dict(device)
            row["_parent"] = parent
            rows.append(row)
            rows.extend(BackupManager._flatten_block_devices(device.get("children"), row))
        return rows

    def _block_devices(self):
        args = [
            LSBLK,
            "--json",
            "--bytes",
            "--output",
            "NAME,PATH,PKNAME,LABEL,SIZE,MOUNTPOINTS",
        ]
        try:
            result = self.command(args, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise BackupStatusError(
                f"storage discovery timed out after {self.timeout:g} seconds"
            ) from exc
        except OSError as exc:
            raise BackupStatusError(f"could not inspect storage: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or "lsblk failed").strip()[-500:]
            raise BackupStatusError(detail)
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise BackupStatusError("storage discovery returned invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("blockdevices"), list):
            raise BackupStatusError("storage discovery returned an invalid schema")
        return self._flatten_block_devices(payload["blockdevices"])

    @staticmethod
    def _mountpoints(row):
        points = row.get("mountpoints")
        if not isinstance(points, list):
            points = [row.get("mountpoint")]
        return [point for point in points if isinstance(point, str) and point]

    @staticmethod
    def _root_row(row):
        current = row
        while current.get("_parent") is not None:
            current = current["_parent"]
        return current

    @staticmethod
    def _descendants(row):
        values = [row]
        for child in row.get("children") or ():
            if isinstance(child, dict):
                values.extend(BackupManager._descendants(child))
        return values

    @staticmethod
    def _stamp(path):
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise BackupStatusError(f"could not read backup stamp: {exc}") from exc
        return int(stat.st_mtime)

    @staticmethod
    def _plist_timestamp(value):
        if not isinstance(value, datetime.datetime):
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.timezone.utc)
        return int(value.timestamp())

    def _time_machine(self, now):
        bundle = self.time_machine_bundle
        result = {
            "device": os.path.basename(bundle).removesuffix(".sparsebundle") or "Mac",
            "available": os.path.isdir(bundle),
            "last_backup_at": None,
            "snapshots": [],
            "running": False,
            "progress_percent": None,
            "bytes_copied": None,
            "total_bytes": None,
            "updated_at": None,
            "error": None,
        }
        if not result["available"]:
            result["error"] = "Time Machine sparsebundle is not reachable"
            return result

        history_path = os.path.join(bundle, "com.apple.TimeMachine.SnapshotHistory.plist")
        try:
            with open(history_path, "rb") as handle:
                history = plistlib.load(handle)
            raw_snapshots = history.get("Snapshots", []) if isinstance(history, dict) else []
            timestamps = [
                self._plist_timestamp(item.get("com.apple.backupd.SnapshotCompletionDate"))
                for item in raw_snapshots
                if isinstance(item, dict)
            ]
            result["snapshots"] = sorted(
                (stamp for stamp in timestamps if stamp is not None), reverse=True
            )[:24]
            if result["snapshots"]:
                result["last_backup_at"] = result["snapshots"][0]
        except FileNotFoundError:
            result["error"] = "Time Machine snapshot history is missing"
        except (OSError, ValueError, TypeError) as exc:
            result["error"] = f"could not read Time Machine history: {exc}"

        results_path = os.path.join(bundle, "com.apple.TimeMachine.Results.plist")
        try:
            updated_at = int(os.stat(results_path).st_mtime)
            with open(results_path, "rb") as handle:
                current = plistlib.load(handle)
            if isinstance(current, dict):
                progress = current.get("Progress")
                progress = progress if isinstance(progress, dict) else {}
                percent = progress.get("Percent")
                if isinstance(percent, (int, float)) and not isinstance(percent, bool):
                    result["progress_percent"] = round(max(0, min(1, percent)) * 100, 1)
                for source, target in (("bytes", "bytes_copied"), ("totalBytes", "total_bytes")):
                    value = progress.get(source)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        result[target] = value
                # A stale Results.plist can retain Running=true after an interrupted
                # backup. It must have been updated recently to count as live.
                result["running"] = current.get("Running") is True and now - updated_at <= 900
                result["updated_at"] = updated_at
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            if result["error"] is None:
                result["error"] = f"could not read Time Machine progress: {exc}"
        return result

    def status(self):
        now = int(self.wall_clock())
        configuration = self._configuration()
        rows = self._block_devices()
        labels = {row.get("label"): row for row in rows if row.get("label")}
        clone_factor = configuration["clone_stale_factor"]
        hotswaps = []
        for target in configuration["targets"]:
            label = target["label"]
            row = labels.get(label)
            root = self._root_row(row) if row is not None else None
            mounts = [] if root is None else [
                point
                for device in self._descendants(root)
                for point in self._mountpoints(device)
            ]
            last_clone_at = self._stamp(os.path.join(self.stamp_dir, f"clone_{label}"))
            interval_seconds = target["interval_days"] * 86400
            age_seconds = None if last_clone_at is None else max(0, now - last_clone_at)
            hotswaps.append(
                {
                    **target,
                    "attached": row is not None,
                    "device": root.get("path") if root is not None else None,
                    "size_bytes": root.get("size") if root is not None else None,
                    "mounted": bool(mounts),
                    "mountpoints": mounts,
                    "last_clone_at": last_clone_at,
                    "due": last_clone_at is None or age_seconds >= interval_seconds,
                    "stale": last_clone_at is None
                    or age_seconds > interval_seconds * clone_factor,
                }
            )

        borg_at = self._stamp(os.path.join(self.stamp_dir, "borg_ok"))
        borg_stale_seconds = configuration["borg_stale_hours"] * 3600
        borg = {
            "last_success_at": borg_at,
            "stale_hours": configuration["borg_stale_hours"],
            "stale": borg_at is None or now - borg_at > borg_stale_seconds,
        }
        time_machine = self._time_machine(now)
        with self.lock:
            operation = copy.deepcopy(self.operation)
        attention = borg["stale"] or any(card["stale"] for card in hotswaps)
        if not time_machine["available"] or time_machine["last_backup_at"] is None:
            attention = True
        health = "running" if operation["status"] == "running" or time_machine["running"] else (
            "attention" if attention else "good"
        )
        return {
            "checked_at": now,
            "health": health,
            "borg": borg,
            "hotswaps": hotswaps,
            "time_machine": time_machine,
            "operation": operation,
        }

    def start_clone(self, target):
        configuration = self._configuration()
        allowed = {item["label"] for item in configuration["targets"]}
        if target not in allowed:
            raise ValueError("unknown hotspare target")
        current = self.status()
        card = next(item for item in current["hotswaps"] if item["label"] == target)
        if not card["attached"]:
            raise BackupStatusError(f"{target} is not attached")
        if card["mounted"]:
            raise BackupStatusError(f"{target} has mounted partitions")
        with self.lock:
            if self.operation["status"] == "running":
                raise BackupStatusError("another dashboard clone is already running")
            started_at = int(self.wall_clock())
            self.operation = {
                "status": "running",
                "target": target,
                "started_at": started_at,
                "completed_at": None,
                "error": None,
            }
            self.thread = threading.Thread(
                target=self._run_clone,
                args=(target, started_at),
                name="backup-clone",
                daemon=True,
            )
            self.thread.start()
        return self.status()

    def _run_clone(self, target, started_at):
        error = None
        try:
            result = self.command(
                [SUDO, "-n", self.clone_tool, target], timeout=self.clone_timeout
            )
            if result.returncode:
                detail = (result.stderr or result.stdout or "clone failed").strip()[-500:]
                error = detail
        except subprocess.TimeoutExpired:
            error = f"clone timed out after {self.clone_timeout:g} seconds"
        except OSError as exc:
            error = f"could not start clone: {exc}"
        with self.lock:
            if self.operation.get("started_at") == started_at:
                self.operation.update(
                    {
                        "status": "error" if error else "complete",
                        "completed_at": int(self.wall_clock()),
                        "error": error,
                    }
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


class DiskCommandError(RuntimeError):
    pass


class DiskManager:
    """Report configured USB disks and run label-only lifecycle actions."""

    LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

    def __init__(
        self,
        config=DISK_POLICY_CONF,
        control=DISKCTL,
        hold_dir=DISK_EJECT_HOLD_DIR,
        command=run_command,
        timeout=DISK_STATUS_TIMEOUT,
        action_timeout=DISK_ACTION_TIMEOUT,
        wall_clock=time.time,
    ):
        self.config = config
        self.control = control
        self.hold_dir = hold_dir
        self.command = command
        self.timeout = timeout
        self.action_timeout = action_timeout
        self.wall_clock = wall_clock
        self.lock = threading.Lock()
        self.thread = None
        self.operation = {
            "status": "idle",
            "action": None,
            "label": None,
            "started_at": None,
            "completed_at": None,
            "error": None,
        }

    @classmethod
    def _parse_array(cls, text, name):
        match = re.search(
            rf"^\s*{re.escape(name)}=\((.*?)^\s*\)",
            text,
            re.MULTILINE | re.DOTALL,
        )
        if not match:
            raise DiskCommandError(f"disk policy has no {name}")
        try:
            labels = shlex.split(match.group(1), comments=True, posix=True)
        except ValueError as exc:
            raise DiskCommandError(f"could not parse {name}: {exc}") from exc
        if not labels or len(labels) != len(set(labels)) or any(
            not cls.LABEL_RE.fullmatch(label) for label in labels
        ):
            raise DiskCommandError(f"disk policy has invalid {name}")
        return labels

    @classmethod
    def parse_config(cls, text):
        mount_labels = cls._parse_array(text, "MOUNT_LABELS")
        always_mount_labels = cls._parse_array(text, "ALWAYS_MOUNT_LABELS")
        manual_mount_labels = cls._parse_array(text, "MANUAL_MOUNT_LABELS")
        hdd_labels = cls._parse_array(text, "HDD_LABELS")
        if not set(always_mount_labels).issubset(mount_labels):
            raise DiskCommandError("ALWAYS_MOUNT_LABELS must be a subset of MOUNT_LABELS")
        controllable_labels = [
            *mount_labels,
            *(label for label in manual_mount_labels if label not in mount_labels),
        ]
        observed = [
            *controllable_labels,
            *(label for label in hdd_labels if label not in controllable_labels),
        ]
        return {
            "mount_labels": mount_labels,
            "always_mount_labels": always_mount_labels,
            "manual_mount_labels": manual_mount_labels,
            "controllable_labels": controllable_labels,
            "hdd_labels": hdd_labels,
            "labels": observed,
        }

    def _configuration(self):
        try:
            return self.parse_config(read_text_file(self.config))
        except OSError as exc:
            raise DiskCommandError(f"could not read disk policy: {exc}") from exc

    def _block_devices(self):
        args = [
            LSBLK,
            "--json",
            "--bytes",
            "--output",
            "NAME,PATH,PKNAME,LABEL,PARTLABEL,FSTYPE,SIZE,TRAN,MOUNTPOINTS,TYPE",
        ]
        try:
            result = self.command(args, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise DiskCommandError(
                f"disk discovery timed out after {self.timeout:g} seconds"
            ) from exc
        except OSError as exc:
            raise DiskCommandError(f"could not inspect disks: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or "lsblk failed").strip()[-500:]
            raise DiskCommandError(detail)
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise DiskCommandError("disk discovery returned invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("blockdevices"), list):
            raise DiskCommandError("disk discovery returned an invalid schema")
        return BackupManager._flatten_block_devices(payload["blockdevices"])

    def _hold(self, label, now):
        path = os.path.join(self.hold_dir, label)
        try:
            if os.path.islink(path):
                return {"until": None, "remaining_seconds": None, "error": "unsafe hold marker"}
            with open(path, encoding="ascii") as handle:
                raw = handle.readline().strip()
        except FileNotFoundError:
            return {"until": None, "remaining_seconds": 0, "error": None}
        except OSError as exc:
            return {"until": None, "remaining_seconds": None, "error": str(exc)}
        if not re.fullmatch(r"[1-9][0-9]{0,10}", raw):
            return {"until": None, "remaining_seconds": None, "error": "malformed hold marker"}
        deadline = int(raw)
        if deadline <= now:
            return {"until": None, "remaining_seconds": 0, "error": None}
        return {
            "until": deadline,
            "remaining_seconds": deadline - now,
            "error": None,
        }

    @staticmethod
    def _row_mounts(row):
        points = row.get("mountpoints")
        if not isinstance(points, list):
            points = [row.get("mountpoint")]
        return [point for point in points if isinstance(point, str) and point]

    def status(self):
        now = int(self.wall_clock())
        configuration = self._configuration()
        rows = self._block_devices()
        mount_labels = set(configuration["mount_labels"])
        always_mount_labels = set(configuration["always_mount_labels"])
        controllable_labels = set(configuration["controllable_labels"])
        disks = []
        for label in configuration["labels"]:
            matches = [
                row
                for row in rows
                if row.get("label") == label or row.get("partlabel") == label
            ]
            # LABEL and PARTLABEL can both match the same partition; flatten
            # yields it only once. Multiple rows are an ambiguous unsafe state.
            row = matches[0] if len(matches) == 1 else None
            root = BackupManager._root_row(row) if row is not None else None
            mounts = self._row_mounts(row) if row is not None else []
            expected_mount = f"/mnt/{label}"
            hold = self._hold(label, now) if label in mount_labels else {
                "until": None,
                "remaining_seconds": 0,
                "error": None,
            }
            error = None
            if len(matches) > 1:
                error = "label resolves to multiple devices"
            elif root is not None and root.get("tran") != "usb":
                error = "label is not on a USB disk"
            elif any(point != expected_mount for point in mounts):
                error = "mounted at an unexpected path"
            elif hold["error"]:
                error = hold["error"]
            attached = row is not None
            mounted = expected_mount in mounts
            disks.append(
                {
                    "label": label,
                    "role": (
                        "always"
                        if label in always_mount_labels
                        else "policy" if label in mount_labels else "backup"
                    ),
                    "automatic_mount": label in mount_labels,
                    "requires_disk_policy": label not in always_mount_labels,
                    "controllable": label in controllable_labels,
                    "attached": attached,
                    "mounted": mounted,
                    "mountpoints": mounts,
                    "device": root.get("path") if root is not None else None,
                    "size_bytes": root.get("size") if root is not None else None,
                    "filesystem": row.get("fstype") if row is not None else None,
                    "expected_mount": expected_mount,
                    "hold_until": hold["until"],
                    "hold_remaining_seconds": hold["remaining_seconds"],
                    "error": error,
                }
            )
        with self.lock:
            operation = copy.deepcopy(self.operation)
        return {"checked_at": now, "disks": disks, "operation": operation}

    def start_action(self, label, action):
        configuration = self._configuration()
        if label not in configuration["controllable_labels"]:
            raise ValueError("unknown controllable disk label")
        if action not in ("eject", "mount"):
            raise ValueError("unknown disk action")
        current = self.status()
        disk = next(item for item in current["disks"] if item["label"] == label)
        if disk["error"]:
            raise DiskCommandError(f"{label}: {disk['error']}")
        if not disk["attached"]:
            raise DiskCommandError(f"{label} is not attached")
        if action == "eject" and not disk["mounted"]:
            raise DiskCommandError(f"{label} is not mounted")
        if action == "mount" and disk["mounted"]:
            raise DiskCommandError(f"{label} is already mounted")
        with self.lock:
            if self.operation["status"] == "running":
                raise DiskCommandError("another disk action is already running")
            started_at = int(self.wall_clock())
            self.operation = {
                "status": "running",
                "action": action,
                "label": label,
                "started_at": started_at,
                "completed_at": None,
                "error": None,
            }
            self.thread = threading.Thread(
                target=self._run_action,
                args=(label, action, started_at),
                name="disk-action",
                daemon=True,
            )
            self.thread.start()
        return self.status()

    def _run_action(self, label, action, started_at):
        error = None
        try:
            result = self.command(
                [self.control, action, label], timeout=self.action_timeout
            )
            if result.returncode:
                error = (result.stderr or result.stdout or "disk action failed").strip()[-500:]
        except subprocess.TimeoutExpired:
            error = f"disk action timed out after {self.action_timeout:g} seconds"
        except OSError as exc:
            error = f"could not start disk action: {exc}"
        with self.lock:
            if self.operation.get("started_at") == started_at:
                self.operation.update(
                    {
                        "status": "error" if error else "complete",
                        "completed_at": int(self.wall_clock()),
                        "error": error,
                    }
                )


class SystemPowerError(RuntimeError):
    pass


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


app = Flask(__name__)
state_store = StateStore()
engine_monitor = EngineMonitor()
cop_alert = CopAlertManager(state_store, engine_monitor)
cop_led = CopLedManager(state_store, engine_monitor)
sonos = SonosController(state_store)
connectivity = ConnectivityMonitor()
openwrt_clients = OpenWrtClientsController()
ubnt_wifi = UbntWifiController(on_change=connectivity.request_refresh)
speedtest = SpeedTestManager()
starlink = TuyaSwitchManager("starlink")
storage_policy = StoragePolicyManager()
lighting = LightingController()
price_checks = PriceCheckController()
system_monitor = SystemMonitorClient()
compute_monitor = ComputeMetricsReader(COMPUTE_ROOT)
usb_devices = UsbDeviceMonitor()
usb_ports = UsbPortController(usb_devices)
backups = BackupManager()
ignition_monitor_control = IgnitionMonitorController()
disk_manager = DiskManager()
system_power = SystemPowerController()


def api_error(message, status):
    return jsonify({"ok": False, "message": str(message)}), status


def request_boolean(name):
    raw = request.values.get(name, "").strip().lower()
    if raw not in ("1", "0", "true", "false", "on", "off"):
        raise ValueError(f"{name} must be true or false")
    return raw in ("1", "true", "on")


@app.before_request
def reject_cross_origin_mutations():
    """Block browser CSRF against dashboard mutation endpoints.

    Command-line clients without browser Origin/Referer headers remain usable.
    The custom header also forces a cross-origin fetch to preflight, and this
    server intentionally grants no cross-origin access.
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
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


@app.route("/api/disks")
def api_disks():
    if request.args:
        return api_error("disk status does not accept input", 400)
    try:
        status = disk_manager.status()
    except DiskCommandError as exc:
        return api_error(f"disk status unavailable: {exc}", 503)
    response = jsonify({"ok": True, "disk_status": status})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/disks/action", methods=["POST"])
def api_disk_action():
    if not _exact_form(("label", "action")):
        return api_error("disk action requires one label and action", 400)
    try:
        status = disk_manager.start_action(request.form["label"], request.form["action"])
    except ValueError as exc:
        return api_error(str(exc), 400)
    except DiskCommandError as exc:
        return api_error(f"could not start disk action: {exc}", 409)
    response = jsonify(
        {
            "ok": True,
            "message": (
                f"{'Unmount' if request.form['action'] == 'eject' else 'Mount'} "
                f"started for {request.form['label']}"
            ),
            "disk_status": status,
        }
    )
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/system-power", methods=["GET", "POST"])
def api_system_power():
    if request.method == "GET":
        if request.args:
            return api_error("system power status does not accept input", 400)
        response = jsonify(
            {"ok": True, "system_power": system_power.snapshot()}
        )
        response.headers["Cache-Control"] = "no-store"
        return response
    if not _exact_form(("action", "confirmation")):
        return api_error(
            "system power action requires one action and confirmation", 400
        )
    action = request.form["action"]
    if request.form["confirmation"] != action:
        return api_error("system power action was not confirmed", 400)
    try:
        status = system_power.start_action(action)
    except ValueError as exc:
        return api_error(str(exc), 400)
    except SystemPowerError as exc:
        return api_error(f"could not start system power action: {exc}", 409)
    label = "Reboot" if action == "reboot" else "Power down"
    response = jsonify(
        {
            "ok": True,
            "message": f"{label} started; safely unmounting disks first",
            "system_power": status,
        }
    )
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store"
    return response


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


@app.route("/api/openwrt/clients")
def api_openwrt_clients():
    try:
        clients = openwrt_clients.status()
    except OpenWrtClientsError as exc:
        return api_error(f"could not query OpenWrt clients: {exc}", 502)
    response = jsonify({"ok": True, "openwrt": clients})
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


@app.route("/api/usb-devices")
def api_usb_devices():
    if request.args:
        return api_error("USB status does not accept input", 400)
    usb_state = usb_devices.refresh()
    response = jsonify(
        {
            "ok": True,
            "usb": usb_state,
            "usb_ports": usb_ports.refresh(usb_state),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/usb-ports/action", methods=["POST"])
def api_usb_port_action():
    if request.args or set(request.form) != {"port", "action"}:
        return api_error("USB port action requires only port and action", 400)
    try:
        state = usb_ports.start_action(
            request.form.get("port", ""), request.form.get("action", "")
        )
    except ValueError as exc:
        return api_error(str(exc), 400)
    except RuntimeError as exc:
        return api_error(str(exc), 409)
    response = jsonify(
        {
            "ok": True,
            "message": "USB port action started",
            "usb_ports": state,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/usb-ports/recover", methods=["POST"])
def api_usb2_recovery():
    if request.args or request.form:
        return api_error("USB 2 recovery does not accept input", 400)
    try:
        state = usb_ports.start_recovery()
    except RuntimeError as exc:
        return api_error(str(exc), 409)
    response = jsonify(
        {
            "ok": True,
            "message": "USB 2 recovery started",
            "usb_ports": state,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/backups")
def api_backups():
    if request.args:
        return api_error("backup status does not accept input", 400)
    try:
        status = backups.status()
    except BackupStatusError as exc:
        return api_error(f"backup status unavailable: {exc}", 503)
    response = jsonify({"ok": True, "backups": status})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/backups/clone", methods=["POST"])
def api_backup_clone():
    if not _exact_form(("target",)):
        return api_error("backup clone requires one hotspare target", 400)
    try:
        status = backups.start_clone(request.form["target"])
    except ValueError as exc:
        return api_error(str(exc), 400)
    except BackupStatusError as exc:
        return api_error(f"could not start clone: {exc}", 409)
    response = jsonify(
        {
            "ok": True,
            "message": f"Clone to {request.form['target']} started",
            "backups": status,
        }
    )
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/ignition-monitor")
def api_ignition_monitor():
    if request.args:
        return api_error("ignition monitor status does not accept input", 400)
    try:
        status = ignition_monitor_control.status()
    except IgnitionMonitorCommandError as exc:
        return api_error(f"ignition monitor unavailable: {exc}", 503)
    response = jsonify({"ok": True, "ignition_monitor": status})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/ignition-monitor/disable", methods=["POST"])
def api_ignition_monitor_disable():
    if not _exact_form(("minutes",)) or not request.form["minutes"].isdigit():
        return api_error("ignition monitor disable requires a duration in minutes", 400)
    try:
        minutes = int(request.form["minutes"])
        status = ignition_monitor_control.disable(minutes)
    except ValueError as exc:
        return api_error(str(exc), 400)
    except IgnitionMonitorCommandError as exc:
        return api_error(f"could not disable ignition monitoring: {exc}", 502)
    return jsonify(
        {
            "ok": True,
            "message": "Ignition monitoring paused",
            "ignition_monitor": status,
        }
    )


@app.route("/api/ignition-monitor/enable", methods=["POST"])
def api_ignition_monitor_enable():
    if not _exact_form(()):
        return api_error("ignition monitor enable does not accept input", 400)
    try:
        status = ignition_monitor_control.enable()
    except IgnitionMonitorCommandError as exc:
        return api_error(f"could not enable ignition monitoring: {exc}", 502)
    return jsonify(
        {
            "ok": True,
            "message": "Ignition monitoring resumed",
            "ignition_monitor": status,
        }
    )


@app.route("/api/price-checks")
def api_price_checks():
    try:
        payload = price_checks.status()
    except PriceCheckCommandError as exc:
        return api_error(f"could not read price checks: {exc}", 502)
    try:
        payload["schedule"] = price_checks.schedule()["schedule"]
    except PriceCheckCommandError as exc:
        payload["schedule"] = {
            "expression": "",
            "description": "",
            "error": f"could not read price-check schedule: {exc}",
            "error_code": "parse",
        }
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/system-monitor")
def api_system_monitor():
    if set(request.args) - {"hours"} or len(request.args.getlist("hours")) > 1:
        return api_error("system monitor accepts only one hours value", 400)
    raw_hours = request.args.get("hours", "6")
    try:
        hours = int(raw_hours)
    except (TypeError, ValueError):
        return api_error("system monitor range must be 6, 24, 168, or 720 hours", 400)
    if hours not in (6, 24, 168, 720):
        return api_error("system monitor range must be 6, 24, 168, or 720 hours", 400)
    try:
        payload = system_monitor.report(hours)
    except SystemMonitorCommandError as exc:
        return api_error(f"system monitor unavailable: {exc}", 503)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/system-monitor/crashes")
def api_system_monitor_crashes():
    if request.args:
        return api_error("crash history does not accept query parameters", 400)
    try:
        payload = system_monitor.crash_history(20)
    except SystemMonitorCommandError as exc:
        return api_error(f"crash history unavailable: {exc}", 503)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/compute")
def api_compute():
    if set(request.args) - {"hours"} or len(request.args.getlist("hours")) > 1:
        return api_error("compute metrics accepts only one hours value", 400)
    raw_hours = request.args.get("hours", "168")
    try:
        hours = int(raw_hours)
    except (TypeError, ValueError):
        return api_error("compute metrics range must be 6, 24, 168, or 720 hours", 400)
    if hours not in (6, 24, 168, 720):
        return api_error("compute metrics range must be 6, 24, 168, or 720 hours", 400)
    try:
        payload = compute_monitor.report(hours)
    except (OSError, ComputeMetricsError) as exc:
        return api_error(f"compute metrics unavailable: {exc}", 503)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/compute/jobs/<job_id>")
def api_compute_job(job_id):
    if request.args:
        return api_error("compute job details do not accept query parameters", 400)
    try:
        payload = compute_monitor.job_details(job_id)
    except ValueError as exc:
        return api_error(str(exc), 400)
    except FileNotFoundError:
        return api_error("compute job not found", 404)
    except (OSError, ComputeMetricsError) as exc:
        return api_error(f"compute job details unavailable: {exc}", 503)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/system-monitor/crash-analysis", methods=["POST"])
def api_system_monitor_crash_analysis():
    if not _exact_form(()):
        return api_error("crash analysis does not accept parameters", 400)
    try:
        payload = system_monitor.crash_analysis()
    except SystemMonitorCommandError as exc:
        return api_error(f"crash analysis unavailable: {exc}", 503)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/price-checks/add", methods=["POST"])
def api_price_checks_add():
    if not _exact_form(("parser", "threshold", "url", "title")):
        return api_error("price check requires parser, threshold, URL, and title", 400)
    try:
        payload = price_checks.add(
            request.form["parser"],
            request.form["threshold"],
            request.form["url"],
            request.form["title"],
        )
    except PriceCheckCommandError as exc:
        return api_error(f"could not add price check: {exc}", 400)
    payload["message"] = f"Watching {payload['item']['display_title']}"
    return jsonify(payload)


@app.route("/api/price-checks/remove", methods=["POST"])
def api_price_checks_remove():
    if not _exact_form(("id",)) or not request.form["id"].isdigit():
        return api_error("price check removal requires an item ID", 400)
    try:
        payload = price_checks.remove(request.form["id"])
    except PriceCheckCommandError as exc:
        return api_error(f"could not remove price check: {exc}", 400)
    payload["message"] = f"Removed {payload['removed']['display_title']}"
    return jsonify(payload)


@app.route("/api/price-checks/mute", methods=["POST"])
def api_price_checks_mute():
    if (
        not _exact_form(("id", "days"))
        or not request.form["id"].isdigit()
        or not request.form["days"].isdigit()
    ):
        return api_error(
            "notification mute requires an item ID and a non-negative number of days",
            400,
        )
    days = int(request.form["days"])
    try:
        payload = price_checks.mute(request.form["id"], days)
    except PriceCheckCommandError as exc:
        return api_error(f"could not change notification mute: {exc}", 400)
    item = payload["item"]
    if days:
        payload["message"] = (
            f"Muted notifications for {item['display_title']} for {days} "
            f"{'day' if days == 1 else 'days'}"
        )
    else:
        payload["message"] = f"Unmuted notifications for {item['display_title']}"
    return jsonify(payload)


@app.route("/api/price-checks/edit", methods=["POST"])
def api_price_checks_edit():
    fields = ("id", "parser", "threshold", "url", "title")
    if not _exact_form(fields) or not request.form["id"].isdigit():
        return api_error(
            "price check edit requires ID, parser, threshold, URL, and title", 400
        )
    try:
        payload = price_checks.edit(
            request.form["id"],
            request.form["parser"],
            request.form["threshold"],
            request.form["url"],
            request.form["title"],
        )
    except PriceCheckCommandError as exc:
        return api_error(f"could not edit price check: {exc}", 400)
    payload["message"] = f"Updated {payload['item']['display_title']}"
    return jsonify(payload)


@app.route("/api/price-checks/schedule", methods=["POST"])
def api_price_checks_schedule():
    if not _exact_form(("expression",)):
        return api_error("price-check schedule requires one cron expression", 400)
    try:
        payload = price_checks.set_schedule(request.form["expression"])
    except PriceCheckCommandError as exc:
        return api_error(f"could not update price-check schedule: {exc}", 400)
    payload["message"] = (
        f"Schedule updated: {payload['schedule']['description']}"
    )
    return jsonify(payload)


@app.route("/api/price-checks/schedule/parse", methods=["POST"])
def api_price_checks_schedule_parse():
    if not _exact_form(("expression",)):
        return api_error("cron preview requires one expression", 400)
    try:
        payload = price_checks.parse_schedule(request.form["expression"])
    except PriceCheckCommandError as exc:
        return api_error(f"could not parse cron: {exc}", 502)
    return jsonify(payload)


@app.route("/api/price-checks/check", methods=["POST"])
def api_price_checks_check():
    if not _exact_form(("target",)):
        return api_error("price check requires one item ID or all", 400)
    target = request.form["target"]
    if target != "all" and not target.isdigit():
        return api_error("price check target must be an item ID or all", 400)
    try:
        payload = price_checks.check(target)
    except PriceCheckCommandError as exc:
        status = 409 if "already running" in str(exc) else 502
        return api_error(f"could not check price: {exc}", status)
    count = len(payload.get("checked", ()))
    payload["message"] = f"Checked {count} price {'item' if count == 1 else 'items'}"
    return jsonify(payload)


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
