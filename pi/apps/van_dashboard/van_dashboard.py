#!/usr/bin/env python3
"""Phone-friendly control surface for vanpi.

COP ALERT publishes requested intent through a runtime marker, maintains its
exterior alert devices, and emits periodic notifications. A separate guarded
supervisor owns vehicle-network wake operations.

This application does not open CAN sockets, inspect CAN interfaces, or wake the
vehicle. Vehicle telemetry is displayed only through read-only service status.
"""

import copy
import csv
import datetime
import glob
import hashlib
import json
import math
import os
import plistlib
import re
import shlex
import sqlite3
import stat
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
    from van_compute_metrics import (
        ComputeMetricsError,
        ComputeMetricsReader,
    )

# Keep request validation local because the dashboard and van_compute use
# intentionally separate deployment paths. The metrics reader applies the same
# validation before reading queue data.
COMPUTE_TASK_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")

# Domain implementations are kept behind this compatibility facade. The
# explicit branch supports both package imports in tests and the deployed flat
# sibling layout where this file is executed directly.
if __package__:
    from .van_dashboard_common import *
    from .van_dashboard_backups import *
    from .van_dashboard_cop import *
    from .van_dashboard_disks import *
    from .van_dashboard_home import *
    from .van_dashboard_storage import *
    from .van_dashboard_sonos import *
    from .van_dashboard_network import *
    from .van_dashboard_usb import *
    from .van_dashboard_integrations import *
    from .van_dashboard_system import *
    from .van_dashboard_telemetry import *
else:
    from van_dashboard_common import *
    from van_dashboard_backups import *
    from van_dashboard_cop import *
    from van_dashboard_disks import *
    from van_dashboard_home import *
    from van_dashboard_storage import *
    from van_dashboard_sonos import *
    from van_dashboard_network import *
    from van_dashboard_usb import *
    from van_dashboard_integrations import *
    from van_dashboard_system import *
    from van_dashboard_telemetry import *

# Preserve the historical facade-level marker constant for callers and source
# safety checks. The implementation uses the identical value from common.
ACTIVE_MARKER = os.path.join(RUNTIME_DIR, "cop-alert.active")


class TelemetryServiceError(RuntimeError):
    pass


telemetry_service_lock = threading.Lock()


def run_telemetry_service_command(args, label, allowed_codes=(0,)):
    try:
        result = run_command(args, timeout=TELEMETRY_SERVICE_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise TelemetryServiceError(
            f"{label} timed out after {TELEMETRY_SERVICE_TIMEOUT:g} seconds"
        ) from exc
    except OSError as exc:
        raise TelemetryServiceError(f"could not run {label}: {exc}") from exc
    if result.returncode not in allowed_codes:
        detail = (result.stderr or result.stdout or f"{label} failed").strip()[-500:]
        raise TelemetryServiceError(detail)
    return result


def telemetry_service_status():
    result = run_telemetry_service_command(
        [SYSTEMCTL, "is-active", "--quiet", TELEMETRY_SERVICE],
        "telemetry service status",
        allowed_codes=(0, 3),
    )
    return {"available": True, "running": result.returncode == 0}


def toggle_telemetry_service():
    with telemetry_service_lock:
        current = telemetry_service_status()
        action = "stop" if current["running"] else "start"
        run_telemetry_service_command(
            [SUDO, "-n", SYSTEMCTL, action, TELEMETRY_SERVICE],
            f"telemetry service {action}",
        )
        return action, telemetry_service_status()


app = Flask(__name__)
state_store = StateStore()
cop_alert = CopAlertManager(state_store)
cop_can_wake = CopCanWakeStatusReader()
cop_led = CopLedManager(state_store)
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
dashboard_restart = DashboardRestartController()
telemetry_summary = TelemetrySummaryReader()
voltage_check = VoltageCheckManager()


def api_error(message, status):
    return jsonify({"ok": False, "message": str(message)}), status


def telemetry_service_snapshot():
    try:
        return telemetry_service_status()
    except TelemetryServiceError as exc:
        return {
            "available": False,
            "running": False,
            "error": str(exc),
        }


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
            "cop_can_wake": cop_can_wake.snapshot(),
            "cop_led": cop_led.snapshot(),
            "starlink": starlink.snapshot(),
            "system_uptime": read_system_uptime(),
        }
    )


@app.route("/api/telemetry-summary")
def api_telemetry_summary():
    if request.args:
        return api_error("telemetry summary does not accept input", 400)
    response = jsonify(
        {
            "ok": True,
            "battery": telemetry_summary.snapshot(),
            "check": voltage_check.snapshot(),
            "service": telemetry_service_snapshot(),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/telemetry-service")
def api_telemetry_service():
    if request.values:
        return api_error("telemetry service toggle does not accept input", 400)
    try:
        action, status = toggle_telemetry_service()
    except TelemetryServiceError as exc:
        return api_error(f"could not toggle telemetry service: {exc}", 502)
    response = jsonify(
        {
            "ok": True,
            "message": f"Telemetry service {'started' if action == 'start' else 'stopped'}",
            "service": status,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/telemetry-voltage-check", methods=["POST"])
def api_telemetry_voltage_check():
    if request.values:
        return api_error("voltage check does not accept input", 400)
    if not voltage_check.start():
        return api_error("a voltage check is already running", 409)
    response = jsonify(
        {
            "ok": True,
            "check": voltage_check.snapshot(),
            "message": "Voltage check started",
        }
    )
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store"
    return response


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
                f"{'Unmount' if request.form['action'] == 'eject' else 'Filesystem repair' if request.form['action'] == 'repair' else 'Mount'} "
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


@app.post("/api/dashboard-service/restart")
def api_dashboard_service_restart():
    if not _exact_form(("confirmation",)):
        return api_error("dashboard restart requires confirmation", 400)
    if request.form["confirmation"] != "restart-dashboard":
        return api_error("dashboard restart was not confirmed", 400)
    try:
        scheduled = dashboard_restart.restart()
    except DashboardRestartError as exc:
        return api_error(f"could not restart dashboard service: {exc}", 409)
    response = jsonify(
        {
            "ok": True,
            "message": "Dashboard service restart scheduled",
            "dashboard_restart": scheduled,
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


@app.route("/api/lights/hue", methods=["POST"])
def api_lights_hue():
    if not _exact_form(("entity", "hue")):
        return api_error("light hue requires entity and hue", 400)
    entity = request.form["entity"]
    if entity not in lighting.entities:
        return api_error("unknown light entity", 400)
    try:
        hue = int(request.form["hue"])
    except (TypeError, ValueError):
        return api_error("hue must be from 0 to 360", 400)
    if not 0 <= hue <= 360:
        return api_error("hue must be from 0 to 360", 400)
    try:
        status = lighting.set_hue(entity, hue)
    except LightingCommandError as exc:
        return api_error(f"could not set light hue: {exc}", 502)
    return jsonify(
        {
            "ok": True,
            "message": f"Hue set to {hue}°",
            "lighting": status,
        }
    )


@app.route("/api/lights/color-temperature", methods=["POST"])
def api_lights_color_temperature():
    if not _exact_form(("entity", "kelvin")):
        return api_error("light color temperature requires entity and kelvin", 400)
    entity = request.form["entity"]
    if entity not in lighting.entities:
        return api_error("unknown light entity", 400)
    try:
        kelvin = int(request.form["kelvin"])
    except (TypeError, ValueError):
        return api_error("color temperature must be from 2000 to 7000 kelvin", 400)
    if not 2000 <= kelvin <= 7000:
        return api_error("color temperature must be from 2000 to 7000 kelvin", 400)
    try:
        status = lighting.set_color_temperature(entity, kelvin)
    except LightingCommandError as exc:
        return api_error(f"could not set light color temperature: {exc}", 502)
    return jsonify(
        {
            "ok": True,
            "message": f"Color temperature set to {kelvin} K",
            "lighting": status,
        }
    )


@app.route("/api/connectivity")
def api_connectivity():
    if set(request.args) - {"active"} or len(request.args.getlist("active")) > 1:
        return api_error("connectivity accepts only active=1", 400)
    active = request.args.get("active")
    if active not in (None, "1"):
        return api_error("active must be 1", 400)
    if active == "1":
        connectivity.mark_active()
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


@app.route("/api/ubnt-wifi/profile", methods=["POST"])
def api_ubnt_wifi_profile():
    fields = (
        "profile",
        "password",
        "bssid",
        "output_power_dbm",
        "rate_module",
        "rate_auto",
        "rate_mcs",
        "apply_now",
    )
    if not _exact_form(fields):
        return api_error("UBNT profile update has an unexpected schema", 400)
    raw = {name: request.form[name] for name in fields}
    profile = raw["profile"]
    password = raw["password"]
    bssid = raw["bssid"].upper()
    if (
        not profile
        or len(profile.encode("utf-8")) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in profile)
    ):
        raw["password"] = ""
        return api_error("invalid UBNT profile", 400)
    password_size = len(password.encode("utf-8"))
    if password and (
        not 8 <= password_size <= 63
        or any(ord(character) < 32 or ord(character) == 127 for character in password)
    ):
        raw["password"] = ""
        return api_error("WPA password must be blank or 8 to 63 bytes", 400)
    if bssid and not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", bssid):
        raw["password"] = ""
        return api_error("Lock to AP must be blank or a MAC address", 400)
    try:
        output_power = int(raw["output_power_dbm"])
        rate_mcs = int(raw["rate_mcs"])
    except (TypeError, ValueError):
        raw["password"] = ""
        return api_error("invalid UBNT radio setting", 400)
    if not 0 <= output_power <= 23:
        raw["password"] = ""
        return api_error("output power must be 0 to 23 dBm", 400)
    if raw["rate_module"] not in ("atheros", "ewma_ht"):
        raw["password"] = ""
        return api_error("invalid UBNT data-rate module", 400)
    if raw["rate_auto"] not in ("true", "false"):
        raw["password"] = ""
        return api_error("rate auto must be true or false", 400)
    if not 0 <= rate_mcs <= 15:
        raw["password"] = ""
        return api_error("maximum TX rate must be MCS 0 to 15", 400)
    if raw["apply_now"] not in ("true", "false"):
        raw["password"] = ""
        return api_error("apply now must be true or false", 400)
    payload = {
        "profile": profile,
        "password": password,
        "bssid": bssid,
        "output_power_dbm": output_power,
        "rate_module": raw["rate_module"],
        "rate_auto": raw["rate_auto"] == "true",
        "rate_mcs": rate_mcs,
        "apply_now": raw["apply_now"] == "true",
    }
    response = _start_ubnt_operation("update-profile", payload)
    payload["password"] = ""
    raw["password"] = ""
    return response


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
            "usb_ports": usb_ports.snapshot(),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/usb-ports/discover", methods=["POST"])
def api_usb_port_discovery():
    if request.args or request.form:
        return api_error("USB port discovery does not accept input", 400)
    try:
        state = usb_ports.discover()
    except RuntimeError as exc:
        return api_error(str(exc), 409)
    response = jsonify(
        {
            "ok": True,
            "message": "USB port controls loaded",
            "usb_ports": state,
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


def _api_manual_backup(kind):
    if not _exact_form(()):
        return api_error("manual backup does not accept input", 400)
    try:
        if kind == "borg":
            status = backups.start_borg_backup()
            message = "Vanpi Borg backup started"
        else:
            status = backups.start_exfat_backup()
            message = "EXFAT512 snapshot started"
    except BackupStatusError as exc:
        return api_error(f"could not start {kind} backup: {exc}", 409)
    response = jsonify(
        {
            "ok": True,
            "message": message,
            "backups": status,
        }
    )
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/backups/borg", methods=["POST"])
def api_backup_borg():
    return _api_manual_backup("borg")


@app.route("/api/backups/exfat", methods=["POST"])
def api_backup_exfat():
    return _api_manual_backup("exfat")


def _api_stop_backup(kind):
    if request.args or not _exact_form(()):
        return api_error("backup stop does not accept input", 400)
    try:
        status = backups.request_stop(kind)
    except ValueError as exc:
        return api_error(str(exc), 400)
    except BackupStatusError as exc:
        return api_error(f"could not stop {kind} backup: {exc}", 409)
    label = "vanpi Borg backup" if kind == "borg" else "EXFAT512 snapshot"
    response = jsonify(
        {
            "ok": True,
            "message": f"Stopping {label} gracefully",
            "backups": status,
        }
    )
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/backups/borg/stop", methods=["POST"])
def api_stop_borg_backup():
    return _api_stop_backup("borg")


@app.route("/api/backups/exfat/stop", methods=["POST"])
def api_stop_exfat_backup():
    return _api_stop_backup("exfat")


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


@app.route("/api/compute/jobs")
def api_compute_jobs():
    if (
        set(request.args) - {"hours", "task"}
        or len(request.args.getlist("hours")) > 1
        or len(request.args.getlist("task")) > 1
    ):
        return api_error(
            "compute task jobs accept one hours value and one task value", 400
        )
    raw_hours = request.args.get("hours", "168")
    task = request.args.get("task")
    if task is None:
        return api_error("compute task jobs require a task value", 400)
    try:
        hours = int(raw_hours)
    except (TypeError, ValueError):
        return api_error(
            "compute metrics range must be 6, 24, 168, or 720 hours", 400
        )
    if hours not in (6, 24, 168, 720):
        return api_error(
            "compute metrics range must be 6, 24, 168, or 720 hours", 400
        )
    if not COMPUTE_TASK_NAME_RE.fullmatch(task):
        return api_error(
            "compute task must use 1 to 64 lowercase letters, digits, or hyphens",
            400,
        )
    task_reader = getattr(compute_monitor, "jobs_for_task", None)
    if task_reader is None:
        return api_error(
            "compute task filtering requires the matching van_compute metrics release",
            503,
        )
    try:
        payload = task_reader(hours, task)
    except ValueError as exc:
        return api_error(str(exc), 400)
    except (OSError, ComputeMetricsError) as exc:
        return api_error(f"compute task jobs unavailable: {exc}", 503)
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
    search_count = len(payload.get("search_checked", ()))
    parts = []
    if count:
        parts.append(f"{count} price {'item' if count == 1 else 'items'}")
    if search_count:
        parts.append(
            f"{search_count} saved {'search' if search_count == 1 else 'searches'}"
        )
    payload["message"] = f"Checked {' and '.join(parts) or 'nothing'}"
    return jsonify(payload)


@app.route("/api/price-checks/searches/add", methods=["POST"])
def api_price_checks_searches_add():
    if not _exact_form(("parser", "url", "title")):
        return api_error("saved search requires parser, URL, and title", 400)
    try:
        payload = price_checks.add_search(
            request.form["parser"], request.form["url"], request.form["title"]
        )
    except PriceCheckCommandError as exc:
        return api_error(f"could not add saved search: {exc}", 400)
    payload["message"] = f"Watching {payload['search']['display_title']}"
    return jsonify(payload)


@app.route("/api/price-checks/searches/remove", methods=["POST"])
def api_price_checks_searches_remove():
    if not _exact_form(("id",)) or not request.form["id"].isdigit():
        return api_error("saved-search removal requires a search ID", 400)
    try:
        payload = price_checks.remove_search(request.form["id"])
    except PriceCheckCommandError as exc:
        return api_error(f"could not remove saved search: {exc}", 400)
    payload["message"] = (
        f"Removed {payload['removed_search']['display_title']}"
    )
    return jsonify(payload)


@app.route("/api/price-checks/searches/dismiss", methods=["POST"])
def api_price_checks_searches_dismiss():
    if (
        not _exact_form(("id", "item_id"))
        or not request.form["id"].isdigit()
        or not request.form["item_id"].isdigit()
    ):
        return api_error("result dismissal requires a search ID and item ID", 400)
    try:
        payload = price_checks.dismiss_search_result(
            request.form["id"], request.form["item_id"]
        )
    except PriceCheckCommandError as exc:
        return api_error(f"could not dismiss search result: {exc}", 400)
    payload["message"] = f"Dismissed {payload['dismissed_result']['title']}"
    return jsonify(payload)


@app.route("/api/price-checks/searches/check", methods=["POST"])
def api_price_checks_searches_check():
    if not _exact_form(("target",)):
        return api_error("saved-search check requires one search ID or all", 400)
    target = request.form["target"]
    if target != "all" and not target.isdigit():
        return api_error("saved-search target must be a search ID or all", 400)
    try:
        payload = price_checks.check_search(target)
    except PriceCheckCommandError as exc:
        status = 409 if "already running" in str(exc) else 502
        return api_error(f"could not check saved search: {exc}", status)
    count = len(payload.get("search_checked", ()))
    payload["message"] = (
        f"Checked {count} saved {'search' if count == 1 else 'searches'}"
    )
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
