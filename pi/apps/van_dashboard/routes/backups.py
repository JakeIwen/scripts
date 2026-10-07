"""Backup routes."""

from flask import Blueprint, jsonify, request

from ..http import _exact_form, api_error, runtime_proxy
from ..van_dashboard_backups import BackupStatusError


bp = Blueprint("backups", __name__)
backups = runtime_proxy("backups")
time_machine_cloud_control = runtime_proxy("time_machine_cloud_control")



@bp.route("/api/backups")
def api_backups():
    if request.args:
        return api_error("backup status does not accept input", 400)
    try:
        status = backups.status()
    except BackupStatusError as exc:
        return api_error(f"backup status unavailable: {exc}", 503)
    response = jsonify({"ok": True, "backups": status})
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/api/backups/settings/clone-card-size", methods=["POST"])
def api_backup_clone_card_size():
    if request.args or not _exact_form(("nominal_gb",)):
        return api_error("clone card capacity requires one nominal_gb value", 400)
    try:
        status = backups.set_clone_card_nominal_gb(request.form["nominal_gb"])
    except ValueError as exc:
        return api_error(str(exc), 400)
    except BackupStatusError as exc:
        return api_error(f"could not update backup settings: {exc}", 503)
    nominal_gb = status["settings"]["clone_card_nominal_gb"]
    response = jsonify(
        {
            "ok": True,
            "message": f"Bootable clone card capacity set to {nominal_gb}GB",
            "backups": status,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/api/backups/clone", methods=["POST"])
def api_backup_clone():
    if not _exact_form(("target",)):
        return api_error("backup clone requires one hotspare target", 400)
    try:
        status = backups.start_clone(request.form["target"])
    except ValueError as exc:
        return api_error(str(exc), 400)
    except BackupStatusError as exc:
        return api_error(f"could not start clone: {exc}", 409)
    response = jsonify(
        {
            "ok": True,
            "message": f"Clone to {request.form['target']} started",
            "backups": status,
        }
    )
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store"
    return response



def _api_manual_backup(kind):
    if not _exact_form(()):
        return api_error("manual backup does not accept input", 400)
    try:
        if kind == "borg":
            status = backups.start_borg_backup()
            message = "Vanpi Borg backup started"
        else:
            status = backups.start_exfat_backup()
            message = "EXFAT512 snapshot started"
    except BackupStatusError as exc:
        return api_error(f"could not start {kind} backup: {exc}", 409)
    response = jsonify(
        {
            "ok": True,
            "message": message,
            "backups": status,
        }
    )
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/api/backups/borg", methods=["POST"])
def api_backup_borg():
    return _api_manual_backup("borg")



@bp.route("/api/backups/exfat", methods=["POST"])
def api_backup_exfat():
    return _api_manual_backup("exfat")



def _api_stop_backup(kind):
    if request.args or not _exact_form(()):
        return api_error("backup stop does not accept input", 400)
    try:
        status = backups.request_stop(kind)
    except ValueError as exc:
        return api_error(str(exc), 400)
    except BackupStatusError as exc:
        return api_error(f"could not stop {kind} backup: {exc}", 409)
    label = "vanpi Borg backup" if kind == "borg" else "EXFAT512 snapshot"
    response = jsonify(
        {
            "ok": True,
            "message": f"Stopping {label} gracefully",
            "backups": status,
        }
    )
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/api/backups/borg/stop", methods=["POST"])
def api_stop_borg_backup():
    return _api_stop_backup("borg")



@bp.route("/api/backups/exfat/stop", methods=["POST"])
def api_stop_exfat_backup():
    return _api_stop_backup("exfat")


def _time_machine_cloud_action(action):
    fields = ("minutes",) if action == "pause" else ()
    if request.args or not _exact_form(fields) or (request.content_length and not request.form and action == "resume"):
        return api_error("invalid Time Machine control input", 400)
    try:
        time_machine_cloud_control.request(action, request.form.get("minutes"))
    except ValueError as exc:
        return api_error(str(exc), 400)
    except RuntimeError as exc:
        return api_error(str(exc), 503)
    message = ("Time Machine iCloud pause requested" if action == "pause" else
               "Time Machine iCloud resume requested; existing safety checks still apply")
    response = jsonify({"ok": True, "message": message})
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/api/backups/time-machine-icloud/pause", methods=["POST"])
def api_pause_time_machine_cloud():
    return _time_machine_cloud_action("pause")


@bp.route("/api/backups/time-machine-icloud/resume", methods=["POST"])
def api_resume_time_machine_cloud():
    return _time_machine_cloud_action("resume")
