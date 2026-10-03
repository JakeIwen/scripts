"""Home automation routes."""

from flask import Blueprint, jsonify, request

from ..http import _exact_form, api_error, runtime_proxy
from ..van_dashboard_home import LightingCommandError
from ..van_dashboard_storage import PolicyCommandError


bp = Blueprint("home", __name__)
connectivity = runtime_proxy("connectivity")
lighting = runtime_proxy("lighting")
starlink = runtime_proxy("starlink")
storage_policy = runtime_proxy("storage_policy")
ubnt_wifi = runtime_proxy("ubnt_wifi")



@bp.route("/api/starlink", methods=["POST"])
def api_starlink():
    try:
        status = starlink.toggle()
    except ValueError as exc:
        return api_error(exc, 503)
    except RuntimeError as exc:
        return api_error(exc, 502)
    ubnt_wifi.starlink_power_changed(status["state"])
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
            "message": ("Starlink powered on; UBNT will connect when denlink is ready"
                        if status["state"] == "on" else "Starlink power off"),
            "starlink": status,
        }
    )



@bp.route("/api/lights")
def api_lights():
    try:
        status = lighting.status()
    except LightingCommandError as exc:
        return api_error(f"could not read lights: {exc}", 502)
    response = jsonify({"ok": True, "lighting": status})
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/api/lights/power", methods=["POST"])
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



@bp.route("/api/lights/brightness", methods=["POST"])
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



@bp.route("/api/lights/hue", methods=["POST"])
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



@bp.route("/api/lights/color-temperature", methods=["POST"])
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
