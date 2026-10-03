"""Disk routes."""

from flask import Blueprint, jsonify, request

from ..http import _exact_form, api_error, runtime_proxy
from ..van_dashboard_disks import DiskCommandError


bp = Blueprint("disks", __name__)
disk_manager = runtime_proxy("disk_manager")



@bp.route("/api/disks")
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



@bp.route("/api/disks/action", methods=["POST"])
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
