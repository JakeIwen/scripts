"""COP alert and dashboard status routes."""

from flask import Blueprint, jsonify, request

from ..http import api_error, runtime_proxy
from ..van_dashboard_system import read_system_uptime


bp = Blueprint("cop", __name__)

cop_alert = runtime_proxy("cop_alert")
cop_can_wake = runtime_proxy("cop_can_wake")
cop_led = runtime_proxy("cop_led")
starlink = runtime_proxy("starlink")
vonstar = runtime_proxy("vonstar")



@bp.route("/api/status")
def api_status():
    return jsonify(
        {
            "ok": True,
            "cop_alert": cop_alert.snapshot(),
            "cop_can_wake": cop_can_wake.snapshot(),
            "cop_led": cop_led.snapshot(),
            "starlink": starlink.snapshot(),
            "system_uptime": read_system_uptime(),
            "vonstar": vonstar.snapshot(),
        }
    )



@bp.route("/api/cop-alert", methods=["POST"])
def api_cop_alert():
    raw = request.values.get("active", "").strip().lower()
    if raw not in ("1", "0", "true", "false", "on", "off"):
        return api_error("active must be true or false", 400)
    active = raw in ("1", "true", "on")
    status = cop_alert.set_active(active)
    verb = "armed" if active else "disarmed"
    return jsonify({"ok": True, "message": f"COP ALERT {verb}", "cop_alert": status})
