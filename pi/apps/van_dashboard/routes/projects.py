"""Hosted project routes."""

from flask import Blueprint, jsonify, request

from ..http import api_error, runtime_proxy
from ..van_dashboard_projects import HostedProjectConflict


bp = Blueprint("projects", __name__)
hosted_projects = runtime_proxy("hosted_projects")


def project_list_response(projects):
    response = jsonify({"ok": True, "projects": projects})
    response.headers["Cache-Control"] = "no-store"
    return response


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
    return project_list_response(projects)


def json_object_body():
    body = request.get_json(silent=True) if request.is_json else None
    if not isinstance(body, dict):
        raise ValueError("Project input must be a JSON object")
    return body


@bp.post("/api/hosted-projects/scan")
def api_scan_hosted_projects():
    try:
        counts = hosted_projects.apply_report(json_object_body())
    except ValueError as exc:
        return api_error(exc, 400)
    except OSError:
        return api_error("Could not save scan report; please try again", 503)
    return jsonify({"ok": True, **counts})


@bp.post("/api/hosted-projects/<entry_id>")
def api_update_hosted_project(entry_id):
    try:
        projects = hosted_projects.update(entry_id, json_object_body())
    except KeyError:
        return api_error("Unknown hosted project", 404)
    except HostedProjectConflict as exc:
        return api_error(exc, 409)
    except ValueError as exc:
        return api_error(exc, 400)
    except OSError:
        return api_error("Could not save project; please try again", 503)
    return project_list_response(projects)
