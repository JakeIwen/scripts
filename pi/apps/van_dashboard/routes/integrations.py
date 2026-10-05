"""Price, system-monitor, and compute routes."""

import re

from flask import Blueprint, jsonify, request

from van_compute.metrics import ComputeMetricsError

from ..http import _exact_form, api_error, runtime_proxy
from ..van_dashboard_integrations import (
    PriceCheckCommandError,
    SystemMonitorCommandError,
)


bp = Blueprint("integrations", __name__)
compute_monitor = runtime_proxy("compute_monitor")
price_checks = runtime_proxy("price_checks")
system_monitor = runtime_proxy("system_monitor")



COMPUTE_TASK_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")



@bp.route("/api/price-checks")
def api_price_checks():
    try:
        payload = price_checks.status()
    except PriceCheckCommandError as exc:
        return api_error(f"could not read price checks: {exc}", 502)
    try:
        payload["schedule"] = price_checks.schedule()["schedule"]
    except PriceCheckCommandError as exc:
        payload["schedule"] = {
            "expression": "",
            "description": "",
            "error": f"could not read price-check schedule: {exc}",
            "error_code": "parse",
        }
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/api/system-monitor")
def api_system_monitor():
    if set(request.args) - {"hours"} or len(request.args.getlist("hours")) > 1:
        return api_error("system monitor accepts only one hours value", 400)
    raw_hours = request.args.get("hours", "6")
    try:
        hours = int(raw_hours)
    except (TypeError, ValueError):
        return api_error("system monitor range must be 6, 24, 168, or 720 hours", 400)
    if hours not in (6, 24, 168, 720):
        return api_error("system monitor range must be 6, 24, 168, or 720 hours", 400)
    try:
        payload = system_monitor.report(hours)
    except SystemMonitorCommandError as exc:
        return api_error(f"system monitor unavailable: {exc}", 503)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/api/system-monitor/crashes")
def api_system_monitor_crashes():
    if request.args:
        return api_error("crash history does not accept query parameters", 400)
    try:
        payload = system_monitor.crash_history(20)
    except SystemMonitorCommandError as exc:
        return api_error(f"crash history unavailable: {exc}", 503)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/api/compute")
def api_compute():
    if set(request.args) - {"hours"} or len(request.args.getlist("hours")) > 1:
        return api_error("compute metrics accepts only one hours value", 400)
    raw_hours = request.args.get("hours", "168")
    try:
        hours = int(raw_hours)
    except (TypeError, ValueError):
        return api_error("compute metrics range must be 6, 24, 168, or 720 hours", 400)
    if hours not in (6, 24, 168, 720):
        return api_error("compute metrics range must be 6, 24, 168, or 720 hours", 400)
    try:
        payload = compute_monitor.report(hours)
    except (OSError, ComputeMetricsError) as exc:
        return api_error(f"compute metrics unavailable: {exc}", 503)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/api/compute/jobs")
def api_compute_jobs():
    if (
        set(request.args) - {"hours", "task"}
        or len(request.args.getlist("hours")) > 1
        or len(request.args.getlist("task")) > 1
    ):
        return api_error(
            "compute task jobs accept one hours value and one task value", 400
        )
    raw_hours = request.args.get("hours", "168")
    task = request.args.get("task")
    if task is None:
        return api_error("compute task jobs require a task value", 400)
    try:
        hours = int(raw_hours)
    except (TypeError, ValueError):
        return api_error(
            "compute metrics range must be 6, 24, 168, or 720 hours", 400
        )
    if hours not in (6, 24, 168, 720):
        return api_error(
            "compute metrics range must be 6, 24, 168, or 720 hours", 400
        )
    if not COMPUTE_TASK_NAME_RE.fullmatch(task):
        return api_error(
            "compute task must use 1 to 64 lowercase letters, digits, or hyphens",
            400,
        )
    task_reader = getattr(compute_monitor, "jobs_for_task", None)
    if task_reader is None:
        return api_error(
            "compute task filtering requires the matching van_compute metrics release",
            503,
        )
    try:
        payload = task_reader(hours, task)
    except ValueError as exc:
        return api_error(str(exc), 400)
    except (OSError, ComputeMetricsError) as exc:
        return api_error(f"compute task jobs unavailable: {exc}", 503)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/api/compute/jobs/<job_id>")
def api_compute_job(job_id):
    if request.args:
        return api_error("compute job details do not accept query parameters", 400)
    try:
        payload = compute_monitor.job_details(job_id)
    except ValueError as exc:
        return api_error(str(exc), 400)
    except FileNotFoundError:
        return api_error("compute job not found", 404)
    except (OSError, ComputeMetricsError) as exc:
        return api_error(f"compute job details unavailable: {exc}", 503)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/api/system-monitor/crash-analysis", methods=["POST"])
def api_system_monitor_crash_analysis():
    if not _exact_form(()):
        return api_error("crash analysis does not accept parameters", 400)
    try:
        payload = system_monitor.crash_analysis()
    except SystemMonitorCommandError as exc:
        return api_error(f"crash analysis unavailable: {exc}", 503)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/api/price-checks/add", methods=["POST"])
def api_price_checks_add():
    if not _exact_form(("parser", "threshold", "url", "title")):
        return api_error("price check requires parser, threshold, URL, and title", 400)
    try:
        payload = price_checks.add(
            request.form["parser"],
            request.form["threshold"],
            request.form["url"],
            request.form["title"],
        )
    except PriceCheckCommandError as exc:
        return api_error(f"could not add price check: {exc}", 400)
    payload["message"] = f"Watching {payload['item']['display_title']}"
    return jsonify(payload)



@bp.route("/api/price-checks/remove", methods=["POST"])
def api_price_checks_remove():
    if not _exact_form(("id",)) or not request.form["id"].isdigit():
        return api_error("price check removal requires an item ID", 400)
    try:
        payload = price_checks.remove(request.form["id"])
    except PriceCheckCommandError as exc:
        return api_error(f"could not remove price check: {exc}", 400)
    payload["message"] = f"Removed {payload['removed']['display_title']}"
    return jsonify(payload)



@bp.route("/api/price-checks/mute", methods=["POST"])
def api_price_checks_mute():
    if (
        not _exact_form(("id", "days"))
        or not request.form["id"].isdigit()
        or not request.form["days"].isdigit()
    ):
        return api_error(
            "notification mute requires an item ID and a non-negative number of days",
            400,
        )
    days = int(request.form["days"])
    try:
        payload = price_checks.mute(request.form["id"], days)
    except PriceCheckCommandError as exc:
        return api_error(f"could not change notification mute: {exc}", 400)
    item = payload["item"]
    if days:
        payload["message"] = (
            f"Muted notifications for {item['display_title']} for {days} "
            f"{'day' if days == 1 else 'days'}"
        )
    else:
        payload["message"] = f"Unmuted notifications for {item['display_title']}"
    return jsonify(payload)



@bp.route("/api/price-checks/edit", methods=["POST"])
def api_price_checks_edit():
    fields = ("id", "parser", "threshold", "url", "title")
    if not _exact_form(fields) or not request.form["id"].isdigit():
        return api_error(
            "price check edit requires ID, parser, threshold, URL, and title", 400
        )
    try:
        payload = price_checks.edit(
            request.form["id"],
            request.form["parser"],
            request.form["threshold"],
            request.form["url"],
            request.form["title"],
        )
    except PriceCheckCommandError as exc:
        return api_error(f"could not edit price check: {exc}", 400)
    payload["message"] = f"Updated {payload['item']['display_title']}"
    return jsonify(payload)



@bp.route("/api/price-checks/schedule", methods=["POST"])
def api_price_checks_schedule():
    if not _exact_form(("expression",)):
        return api_error("price-check schedule requires one cron expression", 400)
    try:
        payload = price_checks.set_schedule(request.form["expression"])
    except PriceCheckCommandError as exc:
        return api_error(f"could not update price-check schedule: {exc}", 400)
    payload["message"] = (
        f"Schedule updated: {payload['schedule']['description']}"
    )
    return jsonify(payload)



@bp.route("/api/price-checks/schedule/parse", methods=["POST"])
def api_price_checks_schedule_parse():
    if not _exact_form(("expression",)):
        return api_error("cron preview requires one expression", 400)
    try:
        payload = price_checks.parse_schedule(request.form["expression"])
    except PriceCheckCommandError as exc:
        return api_error(f"could not parse cron: {exc}", 502)
    return jsonify(payload)



@bp.route("/api/price-checks/check", methods=["POST"])
def api_price_checks_check():
    if not _exact_form(("target",)):
        return api_error("price check requires one item ID or all", 400)
    target = request.form["target"]
    if target != "all" and not target.isdigit():
        return api_error("price check target must be an item ID or all", 400)
    try:
        payload = price_checks.check(target)
    except PriceCheckCommandError as exc:
        status = 409 if "already running" in str(exc) else 502
        return api_error(f"could not check price: {exc}", status)
    count = len(payload.get("checked", ()))
    search_count = len(payload.get("search_checked", ()))
    parts = []
    if count:
        parts.append(f"{count} price {'item' if count == 1 else 'items'}")
    if search_count:
        parts.append(
            f"{search_count} saved {'search' if search_count == 1 else 'searches'}"
        )
    payload["message"] = f"Checked {' and '.join(parts) or 'nothing'}"
    return jsonify(payload)



@bp.route("/api/price-checks/searches/add", methods=["POST"])
def api_price_checks_searches_add():
    if not _exact_form(("parser", "url", "title")):
        return api_error("saved search requires parser, URL, and title", 400)
    try:
        payload = price_checks.add_search(
            request.form["parser"], request.form["url"], request.form["title"]
        )
    except PriceCheckCommandError as exc:
        return api_error(f"could not add saved search: {exc}", 400)
    payload["message"] = f"Watching {payload['search']['display_title']}"
    return jsonify(payload)



@bp.route("/api/price-checks/searches/remove", methods=["POST"])
def api_price_checks_searches_remove():
    if not _exact_form(("id",)) or not request.form["id"].isdigit():
        return api_error("saved-search removal requires a search ID", 400)
    try:
        payload = price_checks.remove_search(request.form["id"])
    except PriceCheckCommandError as exc:
        return api_error(f"could not remove saved search: {exc}", 400)
    payload["message"] = (
        f"Removed {payload['removed_search']['display_title']}"
    )
    return jsonify(payload)



@bp.route("/api/price-checks/searches/dismiss", methods=["POST"])
def api_price_checks_searches_dismiss():
    if (
        not _exact_form(("id", "item_id"))
        or not request.form["id"].isdigit()
        or not request.form["item_id"].isdigit()
    ):
        return api_error("result dismissal requires a search ID and item ID", 400)
    try:
        payload = price_checks.dismiss_search_result(
            request.form["id"], request.form["item_id"]
        )
    except PriceCheckCommandError as exc:
        return api_error(f"could not dismiss search result: {exc}", 400)
    payload["message"] = f"Dismissed {payload['dismissed_result']['title']}"
    return jsonify(payload)



@bp.route("/api/price-checks/searches/check", methods=["POST"])
def api_price_checks_searches_check():
    if not _exact_form(("target",)):
        return api_error("saved-search check requires one search ID or all", 400)
    target = request.form["target"]
    if target != "all" and not target.isdigit():
        return api_error("saved-search target must be a search ID or all", 400)
    try:
        payload = price_checks.check_search(target)
    except PriceCheckCommandError as exc:
        status = 409 if "already running" in str(exc) else 502
        return api_error(f"could not check saved search: {exc}", status)
    count = len(payload.get("search_checked", ()))
    payload["message"] = (
        f"Checked {count} saved {'search' if count == 1 else 'searches'}"
    )
    return jsonify(payload)
