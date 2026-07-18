#!/usr/bin/env python3
"""Phone-friendly control surface for vanpi.

The first dashboard feature is COP ALERT.  While active it:

* keeps Home Assistant's ``switch.ext_flood`` on while the engine is stopped;
* periodically wakes C-CAN with a benign RF Hub identification read so the
  dash accessory rail (and therefore the dashcam) stays awake;
* emits a bacon ntfy notification every five minutes; and
* publishes fresh, passive C-CAN engine-running evidence for ignition_on.sh.

The CAN receive path is passive.  The only transmitted frame is the explicitly
requested RF Hub ReadDataByIdentifier wake.  This process deliberately does not
bring can0 up/down or change its bitrate/listen-only state because the interface
is shared with other vehicle tooling.
"""

import json
import os
import re
import socket
import struct
import subprocess
import threading
import time
from urllib.parse import urlsplit

from flask import Flask, jsonify, request


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
FLOOD_CHECK_INTERVAL = float(os.environ.get("VAN_DASHBOARD_FLOOD_CHECK_INTERVAL", "15"))

DEFAULT_SONOS_DEVICE = os.environ.get("VAN_DASHBOARD_SONOS_DEVICE", "vonFront")


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


def run_command(args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


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
        self.store.set("cop_alert", active)
        with self.lock:
            self.errors.clear()
            if active:
                self._touch(self.active_marker)
                self.next_wake = 0.0
                self.next_flood_check = 0.0
                self.next_ntfy = 0.0
            else:
                self._remove(self.active_marker)
                self._remove(self.engine_marker)

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
            ok, message = self._send_ntfy()
            if ok:
                with self.lock:
                    self.last_ntfy = int(self.wall_clock())
                self._set_error("ntfy", None)
                self.next_ntfy = now + NTFY_INTERVAL
            else:
                self._set_error("ntfy", message)
                self.next_ntfy = now + min(30.0, NTFY_INTERVAL)

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

    def _send_ntfy(self):
        try:
            result = self.command(
                [NTFY_SEND, "COP ALERT", "🥓 COP ALERT is active", "high", "bacon"],
                timeout=20,
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


class SonosController:
    """Small Sonos grouping/volume controller matching the audiobook page."""

    def __init__(self, store, discover_func=None, clock=time.monotonic):
        self.store = store
        self.discover_func = discover_func
        self.clock = clock
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
        speakers = []
        for name, zone in sorted(zones.items()):
            try:
                volume = zone.volume
            except Exception:
                volume = None
            speakers.append(
                {
                    "name": name,
                    "volume": volume,
                    "grouped": name in members,
                    "coordinator": name == coordinator_name,
                    "group_coordinator": zone.group.coordinator.player_name,
                }
            )
        return {"ok": True, "coordinator": coordinator_name, "speakers": speakers}

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


app = Flask(__name__)
state_store = StateStore()
engine_monitor = EngineMonitor()
cop_alert = CopAlertManager(state_store, engine_monitor)
sonos = SonosController(state_store)


def api_error(message, status):
    return jsonify({"ok": False, "message": str(message)}), status


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
    return jsonify({"ok": True, "cop_alert": cop_alert.snapshot()})


@app.route("/api/cop-alert", methods=["POST"])
def api_cop_alert():
    raw = request.values.get("active", "").strip().lower()
    if raw not in ("1", "0", "true", "false", "on", "off"):
        return api_error("active must be true or false", 400)
    active = raw in ("1", "true", "on")
    status = cop_alert.set_active(active)
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


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Van Dashboard">
<meta name="theme-color" content="#111820">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/app-icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/app-icon.svg">
<title>Van Dashboard</title>
<style>
:root{color-scheme:dark;--bg:#111820;--panel:#19232d;--raised:#22313d;--ink:#eef5f8;
  --dim:#96a8b3;--accent:#51b7c6;--line:#2b3d49;--good:#62c899;--bad:#ef7067;
  --alert:#ef503f;--alert2:#ff9b43;--shadow:0 14px 45px #050a0e88}
*{box-sizing:border-box}html{background:var(--bg)}
body{margin:0;min-height:100vh;padding:env(safe-area-inset-top) 14px calc(28px + env(safe-area-inset-bottom));
  background:radial-gradient(circle at 86% -4%,#275a6b66,transparent 34%),var(--bg);color:var(--ink);
  font:16px/1.4 -apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif}
main,header{width:min(100%,680px);margin:auto}button,input{font:inherit}button{color:inherit;-webkit-tap-highlight-color:transparent}
header{display:flex;align-items:center;justify-content:space-between;padding:24px 2px 18px}
.eyebrow{color:var(--accent);font-size:11px;font-weight:750;letter-spacing:.16em;text-transform:uppercase}
h1{font-size:29px;line-height:1.05;letter-spacing:-.03em;margin:3px 0 0}.van-mark{font-size:28px;filter:grayscale(.2)}
.connection{display:flex;align-items:center;gap:7px;color:var(--dim);font-size:12px;margin:0 2px 14px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--bad);box-shadow:0 0 0 3px #ef706718}
.dot.on{background:var(--good);box-shadow:0 0 0 3px #62c89920}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}
.tile{min-height:150px;border:1px solid var(--line);border-radius:19px;background:linear-gradient(145deg,var(--panel),#151f28);
  padding:15px;text-decoration:none;color:inherit;box-shadow:0 8px 28px #050b102e;display:flex;flex-direction:column;
  align-items:flex-start;text-align:left;cursor:pointer;position:relative;overflow:hidden}
.tile:active{background:var(--raised);transform:scale(.985)}.tile-icon{font-size:31px;line-height:1;margin-bottom:auto}
.tile-title{font-size:17px;font-weight:760;letter-spacing:-.01em}.tile-detail{font-size:12px;color:var(--dim);margin-top:3px;min-height:34px}
.cop{grid-column:1/-1;min-height:210px;border-color:#744238;background:radial-gradient(circle at 92% 5%,#ef503f33,transparent 35%),linear-gradient(145deg,#292124,#1c2026)}
.cop .tile-icon{font-size:38px}.cop .tile-title{font-size:23px}.cop::after{content:"";position:absolute;inset:auto -25% -80% 25%;height:180px;
  background:var(--alert);filter:blur(60px);opacity:0;transition:opacity .25s}.cop.active{border-color:#ef675a;box-shadow:0 10px 40px #ef503f25}
.cop.active::after{opacity:.35}.cop.active .tile-title{color:#ffd7d2}.pill{position:absolute;right:14px;top:14px;border:1px solid var(--line);
  border-radius:99px;padding:4px 8px;font-size:10px;font-weight:800;letter-spacing:.1em;color:var(--dim);background:#10171dcc}
.cop.active .pill{color:#fff1ef;background:#8b2d27;border-color:#bf473d}.status-lines{display:grid;gap:4px;margin-top:12px;width:100%;font-size:11px;color:var(--dim)}
.status-line{display:flex;justify-content:space-between;gap:10px}.status-line span:last-child{text-align:right;color:#c5d1d7;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.speaker-backdrop{position:fixed;z-index:20;inset:0;background:#05090daa;display:flex;align-items:flex-end;opacity:0;pointer-events:none;transition:opacity .2s}
.speaker-backdrop.open{opacity:1;pointer-events:auto}.speaker-sheet{width:min(100%,680px);max-height:min(78vh,680px);overflow:auto;margin:0 auto;
  background:#141e26;border:1px solid #38505e;border-bottom:0;border-radius:22px 22px 0 0;padding:8px 14px calc(18px + env(safe-area-inset-bottom));
  box-shadow:0 -18px 60px #000a;transform:translateY(24px);transition:transform .2s}.speaker-backdrop.open .speaker-sheet{transform:translateY(0)}
.sheet-grabber{width:38px;height:4px;border-radius:3px;background:#4d626e;margin:2px auto 12px}.sheet-head{display:flex;align-items:center;justify-content:space-between;gap:12px}
.sheet-head h2{margin:0;font-size:18px;letter-spacing:-.01em}.sheet-close{border:1px solid var(--line);background:var(--raised);color:var(--ink);border-radius:50%;width:38px;height:38px;font-size:20px}
.sheet-help{color:var(--dim);font-size:12px;margin:3px 0 14px}.speaker-row{display:grid;grid-template-columns:30px minmax(0,1fr) 35px;align-items:center;column-gap:8px;padding:11px 8px;border-top:1px solid var(--line)}
.speaker-check{width:22px;height:22px;margin:0;accent-color:var(--accent)}.speaker-name{border:0;background:transparent;color:var(--ink);padding:2px 0;text-align:left;font-weight:700;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.speaker-name small{display:block;color:var(--dim);font-size:10px;font-weight:500;margin-top:1px}.speaker-level{font-size:11px;color:var(--dim);text-align:right;font-variant-numeric:tabular-nums}
.speaker-volume{grid-column:2/4;width:100%;margin:8px 0 0;accent-color:var(--accent)}.speaker-loading{padding:28px;text-align:center;color:var(--dim)}body.sheet-open{overflow:hidden}
#toast{position:fixed;z-index:30;left:50%;top:calc(12px + env(safe-area-inset-top));transform:translate(-50%,-14px);width:min(calc(100% - 28px),620px);
  background:#22313d;color:var(--ink);border:1px solid #405967;border-radius:13px;padding:11px 14px;box-shadow:var(--shadow);opacity:0;pointer-events:none;transition:.2s;font-size:13px;text-align:center}
#toast.show{opacity:1;transform:translate(-50%,0)}#toast.bad{border-color:#874944;color:#ffd8d4}body.busy [data-action]{pointer-events:none;opacity:.55}
@media(max-width:420px){.grid{gap:9px}.tile{min-height:140px}.cop{min-height:205px}}@media(prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body>
<header><div><div class="eyebrow">vanpi controls</div><h1>Van Dashboard</h1></div><div class="van-mark" aria-hidden="true">🚐</div></header>
<main>
  <div class="connection"><span class="dot" id="dot"></span><span id="connection">Connecting to vanpi…</span></div>
  <div class="grid">
    <button class="tile cop" id="cop" data-action aria-pressed="false">
      <span class="pill" id="cop-pill">OFF</span><span class="tile-icon" aria-hidden="true">🚨</span>
      <span class="tile-title">COP ALERT</span><span class="tile-detail" id="cop-detail">Tap to keep the dashcam awake</span>
      <span class="status-lines">
        <span class="status-line"><span>Engine</span><span id="engine">Checking C-CAN…</span></span>
        <span class="status-line"><span>ext_flood</span><span id="flood">—</span></span>
        <span class="status-line"><span>CAN wake</span><span id="wake">—</span></span>
      </span>
    </button>
    <a class="tile" id="books" data-action href="#"><span class="tile-icon" aria-hidden="true">📖</span><span class="tile-title">Audiobooks</span><span class="tile-detail">Open the Sonos audiobook library</span></a>
    <button class="tile" id="speakers" data-action aria-haspopup="dialog" aria-expanded="false" aria-controls="speaker-panel">
      <span class="tile-icon" aria-hidden="true">🔊</span><span class="tile-title">Sonos</span><span class="tile-detail" id="speaker-summary">Finding speakers…</span>
    </button>
  </div>
</main>
<div class="speaker-backdrop" id="speaker-backdrop">
  <section class="speaker-sheet" id="speaker-panel" role="dialog" aria-modal="true" aria-labelledby="speaker-title">
    <div class="sheet-grabber"></div><div class="sheet-head"><h2 id="speaker-title">Sonos speakers</h2><button class="sheet-close" id="speaker-close" aria-label="Close speaker selector">×</button></div>
    <p class="sheet-help">Tap a name to control its group. Check speakers to group them with the active player.</p>
    <div id="speaker-list"><div class="speaker-loading">Finding speakers…</div></div>
  </section>
</div>
<div id="toast" role="status" aria-live="polite"></div>
<script>
const $=id=>document.getElementById(id);let dashboard=null,speakers=null,busy=false,toastTimer=0;
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function toast(message,bad=false){clearTimeout(toastTimer);const el=$('toast');el.textContent=message;el.className=bad?'show bad':'show';toastTimer=setTimeout(()=>el.className='',3400)}
async function json(url,options){const response=await fetch(url,options);let data;try{data=await response.json()}catch(_){data={message:`Server returned ${response.status}`}}
  if(!response.ok||data.ok===false)throw new Error(data.message||`Request failed (${response.status})`);return data}
async function post(endpoint,params={}){return json('/api/'+endpoint,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','X-Van-Dashboard':'1'},body:new URLSearchParams(params)})}
async function action(work){if(busy)return;busy=true;document.body.classList.add('busy');try{const result=await work();if(result?.message)toast(result.message);await refresh()}
  catch(error){toast(error.message,true)}finally{busy=false;document.body.classList.remove('busy')}}
function age(ts){if(!ts)return 'never';const secs=Math.max(0,Date.now()/1000-ts);return secs<90?`${Math.round(secs)}s ago`:`${Math.round(secs/60)}m ago`}
function updateStatus(data){dashboard=data.cop_alert;const active=dashboard.active,engine=dashboard.engine;$('dot').classList.add('on');$('connection').textContent='Connected · vanpi dashboard';
  $('cop').classList.toggle('active',active);$('cop').setAttribute('aria-pressed',String(active));$('cop-pill').textContent=active?'ACTIVE':'OFF';
  $('cop-detail').textContent=active?'Dashcam wake and 5-minute bacon alerts are active':'Tap to keep the dashcam awake';
  $('engine').textContent=engine.running?`RUNNING · ${Math.round(engine.rpm)} RPM`:engine.rpm===null?'No fresh data':`Stopped · ${Math.round(engine.rpm)} RPM`;
  $('flood').textContent=dashboard.ext_flood;$('wake').textContent=dashboard.last_wake_ok===null?'Not attempted':dashboard.last_wake_ok?`OK · ${age(dashboard.last_wake)}`:'DEGRADED';
  if(active&&dashboard.last_error)$('connection').textContent=`Active with warning · ${dashboard.last_error}`}
async function refresh(){try{updateStatus(await json('/api/status'))}catch(error){$('dot').classList.remove('on');$('connection').textContent=error.message}}
function renderSpeakers(next){speakers=next;const grouped=next.speakers.filter(s=>s.grouped);$('speaker-summary').textContent=`${next.coordinator} · ${grouped.length}/${next.speakers.length} grouped`;
  $('speaker-list').innerHTML=next.speakers.map(s=>{const detail=s.coordinator?'Active coordinator':s.grouped?`Grouped with ${next.coordinator}`:`Group: ${s.group_coordinator}`;
    const volume=Number.isFinite(s.volume)?s.volume:0;return `<div class="speaker-row"><input class="speaker-check" data-action data-group-speaker="${esc(s.name)}" type="checkbox" ${s.grouped?'checked':''} ${s.coordinator?'disabled':''} aria-label="Group ${esc(s.name)}">
      <button class="speaker-name" data-action data-select-speaker="${esc(s.name)}">${esc(s.name)}<small>${esc(detail)}</small></button><span class="speaker-level">${Number.isFinite(s.volume)?s.volume:'—'}</span>
      <input class="speaker-volume" data-action data-speaker-volume="${esc(s.name)}" type="range" min="0" max="100" value="${volume}" ${Number.isFinite(s.volume)?'':'disabled'} aria-label="${esc(s.name)} volume"></div>`}).join('')||'<div class="speaker-loading">No Sonos speakers found</div>'}
async function loadSpeakers(){const next=await json('/api/speakers');renderSpeakers(next);return next}
async function openSpeakers(){$('speaker-backdrop').classList.add('open');document.body.classList.add('sheet-open');$('speakers').setAttribute('aria-expanded','true');
  try{await loadSpeakers()}catch(error){$('speaker-list').innerHTML=`<div class="speaker-loading">${esc(error.message)}</div>`;toast(error.message,true)}}
function closeSpeakers(){$('speaker-backdrop').classList.remove('open');document.body.classList.remove('sheet-open');$('speakers').setAttribute('aria-expanded','false');$('speakers').focus()}
const bookUrl=new URL(window.location.href);bookUrl.port='8787';bookUrl.pathname='/';bookUrl.search='';bookUrl.hash='';$('books').href=bookUrl.toString();
$('cop').addEventListener('click',()=>action(()=>post('cop-alert',{active:dashboard?.active?'false':'true'})));$('speakers').addEventListener('click',openSpeakers);$('speaker-close').addEventListener('click',closeSpeakers);
$('speaker-backdrop').addEventListener('click',event=>{if(event.target===$('speaker-backdrop'))closeSpeakers()});document.addEventListener('keydown',event=>{if(event.key==='Escape')closeSpeakers()});
document.addEventListener('input',event=>{const slider=event.target.closest('[data-speaker-volume]');if(slider)slider.closest('.speaker-row').querySelector('.speaker-level').textContent=slider.value});
document.addEventListener('change',event=>{const checkbox=event.target.closest('[data-group-speaker]');if(checkbox)action(async()=>{try{return await post('speakers/group',{name:checkbox.dataset.groupSpeaker,grouped:checkbox.checked?'1':'0'})}finally{await loadSpeakers()}});
  const slider=event.target.closest('[data-speaker-volume]');if(slider)action(async()=>{try{return await post('speakers/volume',{name:slider.dataset.speakerVolume,volume:slider.value})}finally{await loadSpeakers()}})});
document.addEventListener('click',event=>{const selected=event.target.closest('[data-select-speaker]');if(selected)action(async()=>{const result=await post('speakers/select',{name:selected.dataset.selectSpeaker});await loadSpeakers();return result})});
Promise.allSettled([refresh(),loadSpeakers()]).then(results=>{if(results[1].status==='rejected')$('speaker-summary').textContent='Sonos unavailable'});setInterval(()=>{if(!document.hidden)refresh()},5000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});
</script></body></html>"""


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
    return PAGE


if __name__ == "__main__":
    cop_alert.start()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
