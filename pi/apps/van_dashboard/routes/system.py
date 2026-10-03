"""System control routes."""

from flask import Blueprint, jsonify, request

from ..http import _exact_form, api_error, runtime_proxy
from ..van_dashboard_system import (
    DashboardRestartError,
    IgnitionMonitorCommandError,
    SystemPowerError,
)


bp = Blueprint("system", __name__)
dashboard_restart = runtime_proxy("dashboard_restart")
ignition_monitor_control = runtime_proxy("ignition_monitor_control")
system_power = runtime_proxy("system_power")



@bp.route("/api/system-power", methods=["GET", "POST"])
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



@bp.post("/api/dashboard-service/restart")
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



@bp.route("/api/ignition-monitor")
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



@bp.route("/api/ignition-monitor/disable", methods=["POST"])
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



@bp.route("/api/ignition-monitor/enable", methods=["POST"])
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
