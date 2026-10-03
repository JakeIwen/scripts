"""Vonstar routes."""

from flask import Blueprint, jsonify, request

from ..http import api_error, runtime_proxy
from ..van_dashboard_vonstar import VONSTAR_ACTIONS, VonstarClientError


bp = Blueprint("vonstar", __name__)
vonstar = runtime_proxy("vonstar")



@bp.route("/api/vonstar")
def api_vonstar_status():
    if request.args:
        return api_error("Vonstar status does not accept input", 400)
    response = jsonify({"ok": True, "vonstar": vonstar.snapshot()})
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.post("/api/vonstar")
def api_vonstar_action():
    if set(request.values) != {"action"} or len(request.values.getlist("action")) != 1:
        return api_error("Vonstar requires exactly one action", 400)
    action = request.values["action"]
    try:
        result = vonstar.perform(action)
    except ValueError as exc:
        return api_error(str(exc), 400)
    except VonstarClientError as exc:
        return api_error(str(exc), exc.http_status)
    response = jsonify(
        {
            "ok": True,
            "message": f"{VONSTAR_ACTIONS[action]['label']} completed",
            "result": result,
            "vonstar": vonstar.snapshot(),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.post("/api/vonstar/access-state")
def api_vonstar_access_state():
    if request.args or request.form or request.get_data(cache=True):
        return api_error("Vonstar access-state does not accept input", 400)
    try:
        result = vonstar.read_access_state()
    except VonstarClientError as exc:
        return api_error(str(exc), exc.http_status)
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response
