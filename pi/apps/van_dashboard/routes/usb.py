"""USB routes."""

from flask import Blueprint, jsonify, request

from ..http import api_error, runtime_proxy


bp = Blueprint("usb", __name__)
usb_devices = runtime_proxy("usb_devices")
usb_ports = runtime_proxy("usb_ports")



@bp.route("/api/usb-devices")
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



@bp.route("/api/usb-ports/discover", methods=["POST"])
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



@bp.route("/api/usb-ports/action", methods=["POST"])
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



@bp.route("/api/usb-ports/recover", methods=["POST"])
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
