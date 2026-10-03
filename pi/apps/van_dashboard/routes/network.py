"""Network routes."""

import re

from flask import Blueprint, jsonify, request

from ..http import _exact_form, api_error, runtime_proxy
from ..van_dashboard_network import OpenWrtClientsError


bp = Blueprint("network", __name__)
connectivity = runtime_proxy("connectivity")
openwrt_clients = runtime_proxy("openwrt_clients")
speedtest = runtime_proxy("speedtest")
ubnt_wifi = runtime_proxy("ubnt_wifi")



@bp.route("/api/connectivity")
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



@bp.route("/api/openwrt/clients")
def api_openwrt_clients():
    try:
        clients = openwrt_clients.status()
    except OpenWrtClientsError as exc:
        return api_error(f"could not query OpenWrt clients: {exc}", 502)
    response = jsonify({"ok": True, "openwrt": clients})
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/api/ubnt-wifi")
def api_ubnt_wifi():
    ubnt_wifi.request_refresh()
    response = jsonify({"ok": True, **ubnt_wifi.snapshot()})
    response.headers["Cache-Control"] = "no-store"
    return response



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



@bp.route("/api/ubnt-wifi/scan", methods=["POST"])
def api_ubnt_wifi_scan():
    if request.form:
        return api_error("UBNT scan does not accept input", 400)
    return _start_ubnt_operation("scan")



@bp.route("/api/ubnt-wifi/connect", methods=["POST"])
def api_ubnt_wifi_connect():
    if not _exact_form(("profile",)):
        return api_error("UBNT connect requires one profile", 400)
    profile = request.form["profile"]
    if not profile or len(profile.encode("utf-8")) > 128 or any(
        ord(character) < 32 or ord(character) == 127 for character in profile
    ):
        return api_error("invalid UBNT profile", 400)
    return _start_ubnt_operation("connect", {"profile": profile})



@bp.route("/api/ubnt-wifi/forget", methods=["POST"])
def api_ubnt_wifi_forget():
    if not _exact_form(("profile",)):
        return api_error("UBNT forget requires one profile", 400)
    profile = request.form["profile"]
    if (
        not profile or len(profile.encode("utf-8")) > 128
        or profile.startswith(".") or "/" in profile
        or profile in ("reset", "system.cfg")
        or any(ord(char) < 32 or ord(char) == 127 for char in profile)
    ):
        return api_error("invalid UBNT profile", 400)
    return _start_ubnt_operation("forget", {"profile": profile})



@bp.route("/api/ubnt-wifi/provision", methods=["POST"])
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



@bp.route("/api/ubnt-wifi/resume", methods=["POST"])
def api_ubnt_wifi_resume():
    if request.form:
        return api_error("UBNT resume does not accept input", 400)
    return _start_ubnt_operation("resume")



@bp.route("/api/ubnt-wifi/abort", methods=["POST"])
def api_ubnt_wifi_abort():
    if request.form:
        return api_error("UBNT abort does not accept input", 400)
    if not ubnt_wifi.abort():
        return api_error("no cancellable UBNT Wi-Fi operation is running", 409)
    return (
        jsonify(
            {
                "ok": True,
                "message": "UBNT abort requested",
                **ubnt_wifi.snapshot(),
            }
        ),
        202,
    )



@bp.route("/api/ubnt-wifi/profile", methods=["POST"])
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



@bp.route("/api/speedtest", methods=["GET", "POST"])
def api_speedtest():
    if request.method == "POST":
        started = speedtest.start()
        message = "Speed test started" if started else "Speed test is already running"
        return jsonify({"ok": True, "message": message, "speedtest": speedtest.snapshot()})
    return jsonify({"ok": True, "speedtest": speedtest.snapshot()})
