"""Network history routes."""

from flask import Blueprint, jsonify, request

from ..http import api_error, runtime_proxy
from ..van_dashboard_history import NetworkHistoryError, network_history_query


bp = Blueprint("history", __name__)
network_history = runtime_proxy("network_history")



@bp.route("/api/network-history")
def api_network_history():
    try:
        query = network_history_query(request.args)
        payload = network_history.report(query)
    except ValueError as exc:
        return api_error(str(exc), 400)
    except NetworkHistoryError as exc:
        return api_error(str(exc), 503)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/api/network-history/incidents/<incident_id>")
def api_network_history_incident(incident_id):
    if request.args:
        return api_error("incident details do not accept query parameters", 400)
    try:
        payload = network_history.incident(incident_id)
    except ValueError as exc:
        return api_error(str(exc), 400)
    except NetworkHistoryError as exc:
        return api_error(str(exc), 503)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response
