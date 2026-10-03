"""Telemetry routes and service controls."""

import subprocess
import threading

from flask import Blueprint, jsonify, request

from ..http import api_error, runtime_proxy
from ..van_dashboard_common import (
    SUDO,
    SYSTEMCTL,
    TELEMETRY_SERVICE,
    TELEMETRY_SERVICE_TIMEOUT,
    run_command,
)


bp = Blueprint("telemetry", __name__)
telemetry_summary = runtime_proxy("telemetry_summary")
voltage_check = runtime_proxy("voltage_check")


class TelemetryServiceError(RuntimeError):
    pass


telemetry_service_lock = threading.Lock()



def run_telemetry_service_command(args, label, allowed_codes=(0,)):
    try:
        result = run_command(args, timeout=TELEMETRY_SERVICE_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise TelemetryServiceError(
            f"{label} timed out after {TELEMETRY_SERVICE_TIMEOUT:g} seconds"
        ) from exc
    except OSError as exc:
        raise TelemetryServiceError(f"could not run {label}: {exc}") from exc
    if result.returncode not in allowed_codes:
        detail = (result.stderr or result.stdout or f"{label} failed").strip()[-500:]
        raise TelemetryServiceError(detail)
    return result



def telemetry_service_status():
    result = run_telemetry_service_command(
        [SYSTEMCTL, "is-active", "--quiet", TELEMETRY_SERVICE],
        "telemetry service status",
        allowed_codes=(0, 3),
    )
    return {"available": True, "running": result.returncode == 0}



def toggle_telemetry_service():
    with telemetry_service_lock:
        current = telemetry_service_status()
        action = "stop" if current["running"] else "start"
        run_telemetry_service_command(
            [SUDO, "-n", SYSTEMCTL, action, TELEMETRY_SERVICE],
            f"telemetry service {action}",
        )
        return action, telemetry_service_status()



def telemetry_service_snapshot():
    try:
        return telemetry_service_status()
    except TelemetryServiceError as exc:
        return {
            "available": False,
            "running": False,
            "error": str(exc),
        }



@bp.route("/api/telemetry-summary")
def api_telemetry_summary():
    if request.args:
        return api_error("telemetry summary does not accept input", 400)
    response = jsonify(
        {
            "ok": True,
            "battery": telemetry_summary.snapshot(),
            "check": voltage_check.snapshot(),
            "service": telemetry_service_snapshot(),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.post("/api/telemetry-service")
def api_telemetry_service():
    if request.values:
        return api_error("telemetry service toggle does not accept input", 400)
    try:
        action, status = toggle_telemetry_service()
    except TelemetryServiceError as exc:
        return api_error(f"could not toggle telemetry service: {exc}", 502)
    response = jsonify(
        {
            "ok": True,
            "message": f"Telemetry service {'started' if action == 'start' else 'stopped'}",
            "service": status,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/api/telemetry-voltage-check", methods=["POST"])
def api_telemetry_voltage_check():
    if request.values:
        return api_error("voltage check does not accept input", 400)
    if not voltage_check.start():
        return api_error("a voltage check is already running", 409)
    response = jsonify(
        {
            "ok": True,
            "check": voltage_check.snapshot(),
            "message": "Voltage check started",
        }
    )
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store"
    return response
