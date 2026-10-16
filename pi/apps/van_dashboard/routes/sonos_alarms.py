"""HTTP boundary for native speaker alarms."""

from flask import Blueprint, jsonify, request

from ..http import api_error, runtime_proxy
from ..sonos_alarms import AlarmConflict

bp = Blueprint("sonos_alarms", __name__)
alarms = runtime_proxy("sonos_alarms")


@bp.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/api/speakers/alarms")
def list_alarms():
    try:
        return jsonify(alarms.snapshot())
    except Exception:
        return api_error("Could not read Sonos alarms. Refresh to try again.", 503)


@bp.get("/api/speakers/alarm-favorites")
def list_favorites():
    try:
        return jsonify(ok=True, favorites=[{"id": key, "title": f.title}
                                          for key, f in alarms.favorites().items()])
    except Exception:
        return api_error("Could not load Sonos Favorites. Existing alarm sounds are preserved.", 503)


@bp.post("/api/speakers/alarms/save")
@bp.post("/api/speakers/alarms/enabled")
@bp.post("/api/speakers/alarms/delete")
def mutate_alarm():
    if not request.form or any(len(request.form.getlist(k)) != 1 for k in request.form):
        return api_error("provide alarm settings once each as form data", 400)
    fields = request.form.to_dict()
    alarm_id = fields.pop("id", "")
    revision = fields.pop("revision", "")
    action = request.path.rsplit("/", 1)[-1]
    try:
        if action == "save":
            alarm_id = alarms.save(fields, alarm_id, revision)
        elif action == "enabled":
            if set(fields) != {"enabled"} or fields["enabled"] not in ("0", "1"):
                raise ValueError("enabled must be 0 or 1")
            alarms.set_enabled(alarm_id, revision, fields["enabled"] == "1")
        else:
            if fields:
                raise ValueError("unexpected alarm deletion fields")
            alarms.delete(alarm_id, revision)
    except AlarmConflict as exc:
        return api_error(exc, 409)
    except KeyError as exc:
        return api_error(exc.args[0], 404)
    except ValueError as exc:
        return api_error(exc, 400)
    except Exception:
        # A timeout can follow a successful device write. Never auto-retry it.
        return api_error("Sonos did not confirm the change. Refresh alarms before trying again.", 502)
    return jsonify(ok=True, id=alarm_id, message="Alarm deleted" if action == "delete" else "Alarm saved")
