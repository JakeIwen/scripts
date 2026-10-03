"""Sonos routes."""

import re

from flask import Blueprint, current_app as app, jsonify, request

from ..http import api_error, request_boolean, runtime_proxy


bp = Blueprint("sonos", __name__)
sonos = runtime_proxy("sonos")



@bp.route("/api/speakers")
def api_speakers():
    try:
        return jsonify(sonos.snapshot())
    except Exception as exc:
        return api_error(f"speaker discovery failed: {exc}", 503)



@bp.route("/api/speakers/select", methods=["POST"])
def api_speaker_select():
    name = request.values.get("name", "").strip()
    if not name:
        return api_error("need name", 400)
    try:
        selected = sonos.select(name)
    except KeyError as exc:
        return api_error(exc.args[0], 404)
    except Exception as exc:
        return api_error(f"could not select Sonos group: {exc}", 502)
    return jsonify({"ok": True, "message": f"using {selected}", "device": selected})



@bp.route("/api/speakers/group", methods=["POST"])
def api_speaker_group():
    name = request.values.get("name", "").strip()
    grouped = request.values.get("grouped", "").lower() in ("1", "true", "yes")
    try:
        message = sonos.group(name, grouped)
    except KeyError as exc:
        return api_error(exc.args[0], 404)
    except ValueError as exc:
        return api_error(exc, 400)
    except Exception as exc:
        return api_error(f"could not update Sonos group: {exc}", 502)
    return jsonify({"ok": True, "message": message})



@bp.route("/api/speakers/volume", methods=["POST"])
def api_speaker_volume():
    name = request.values.get("name", "").strip()
    try:
        volume = int(request.values.get("volume", ""))
    except (TypeError, ValueError):
        return api_error("volume must be from 0 to 100", 400)
    try:
        volume = sonos.set_volume(name, volume)
    except KeyError as exc:
        return api_error(exc.args[0], 404)
    except Exception as exc:
        return api_error(f"could not set {name} volume: {exc}", 502)
    return jsonify({"ok": True, "message": f"{name} volume: {volume}", "volume": volume})



@bp.route("/api/speakers/mute", methods=["POST"])
def api_speaker_mute():
    name = request.values.get("name", "").strip()
    try:
        muted = request_boolean("muted")
        muted = sonos.set_mute(name, muted)
    except ValueError as exc:
        return api_error(exc, 400)
    except KeyError as exc:
        return api_error(exc.args[0], 404)
    except Exception as exc:
        return api_error(f"could not mute {name}: {exc}", 502)
    verb = "muted" if muted else "unmuted"
    return jsonify({"ok": True, "message": f"{name} {verb}", "muted": muted})



@bp.route("/api/speakers/group-volume", methods=["POST"])
def api_speaker_group_volume():
    try:
        volume = int(request.values.get("volume", ""))
    except (TypeError, ValueError):
        return api_error("volume must be from 0 to 100", 400)
    try:
        volume = sonos.set_group_volume(volume)
    except Exception as exc:
        return api_error(f"could not set Sonos group volume: {exc}", 502)
    return jsonify({"ok": True, "message": f"Group volume: {volume}", "volume": volume})



@bp.route("/api/speakers/group-mute", methods=["POST"])
def api_speaker_group_mute():
    try:
        muted = request_boolean("muted")
        muted = sonos.set_group_mute(muted)
    except ValueError as exc:
        return api_error(exc, 400)
    except Exception as exc:
        return api_error(f"could not mute Sonos group: {exc}", 502)
    verb = "muted" if muted else "unmuted"
    return jsonify({"ok": True, "message": f"Sonos group {verb}", "muted": muted})



@bp.route("/api/speakers/transport", methods=["POST"])
def api_speaker_transport():
    action = request.values.get("action", "").strip().lower()
    try:
        message = sonos.transport(action)
    except ValueError as exc:
        return api_error(exc, 400)
    except Exception as exc:
        return api_error(f"Sonos transport failed: {exc}", 502)
    return jsonify({"ok": True, "message": message})



@bp.route("/api/speakers/art/<key>")
def api_speaker_art(key):
    if not re.fullmatch(r"[0-9a-f]{16}", key):
        return api_error("invalid Sonos album-art key", 400)
    try:
        content, content_type = sonos.album_art(key)
    except KeyError as exc:
        return api_error(exc.args[0], 404)
    except Exception as exc:
        return api_error(f"Sonos album art unavailable: {exc}", 502)
    response = app.response_class(content, mimetype=content_type)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response
