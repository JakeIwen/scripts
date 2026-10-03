"""Hosted project routes."""

from flask import Blueprint, jsonify, request

from ..http import api_error, runtime_proxy
from ..van_dashboard_projects import HostedProjectConflict


bp = Blueprint("projects", __name__)
hosted_projects = runtime_proxy("hosted_projects")



@bp.route("/api/hosted-projects", methods=["GET", "POST"])
def api_hosted_projects():
    if request.method == "POST":
        if request.mimetype != "application/x-www-form-urlencoded" or any(
            len(request.form.getlist(key)) != 1 for key in request.form
        ):
            return api_error("Project input must be a URL-encoded form with unique fields", 400)
        try:
            projects = hosted_projects.add(request.form.to_dict())
        except HostedProjectConflict as exc:
            return api_error(exc, 409)
        except ValueError as exc:
            return api_error(exc, 400)
        except OSError:
            return api_error("Could not save project; please try again", 503)
    else:
        projects = hosted_projects.snapshot()
    response = jsonify({"ok": True, "projects": projects})
    response.headers["Cache-Control"] = "no-store"
    return response
