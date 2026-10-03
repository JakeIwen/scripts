"""Storage policy routes."""

from flask import Blueprint, jsonify, request

from ..http import api_error, runtime_proxy
from ..van_dashboard_storage import PolicyCommandError, StoragePolicyManager


bp = Blueprint("storage", __name__)
storage_policy = runtime_proxy("storage_policy")



@bp.route("/api/storage-policy", methods=["GET", "POST"])
def api_storage_policy():
    if request.method == "POST":
        expected_form = {"field", "value"}
        if set(request.form) != expected_form or any(
            len(request.form.getlist(name)) != 1 for name in expected_form
        ):
            return api_error("storage policy requires field and boolean value", 400)
        field = request.form.get("field", "")
        raw_value = request.form.get("value", "").lower()
        if field not in StoragePolicyManager.TARGETS:
            return api_error("unknown storage policy field", 400)
        if raw_value not in ("true", "false"):
            return api_error("storage policy value must be true or false", 400)
        try:
            status = storage_policy.update(field, raw_value == "true")
        except PolicyCommandError as exc:
            return api_error(f"could not update storage policy: {exc}", 502)
        label = {
            "disks_enabled": "Disks",
            "torrents_enabled": "Torrents",
            "allow_starlink_torrents": "Starlink torrents",
        }[field]
        state = "enabled" if status[field] else "disabled"
        return jsonify(
            {
                "ok": True,
                "message": f"{label} {state}",
                "policy": status,
            }
        )
    try:
        status = storage_policy.status()
    except PolicyCommandError as exc:
        return api_error(f"could not read storage policy: {exc}", 502)
    return jsonify({"ok": True, "policy": status})
