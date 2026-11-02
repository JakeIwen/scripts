"""Sonos routes."""

import re

from flask import Blueprint, current_app as app, jsonify, request

from ..http import api_error, request_boolean, runtime_proxy
from ..van_dashboard_sonos import SonosNotSeekable, clock_label


bp = Blueprint("sonos", __name__)
sonos = runtime_proxy("sonos")
sonos_loop = runtime_proxy("sonos_loop")

MAX_SEEK_OFFSET = 600



@bp.route("/api/speakers")
def api_speakers():
    try:
        snapshot = sonos.snapshot()
    except Exception as exc:
        return api_error(f"speaker discovery failed: {exc}", 503)
    snapshot["loop"] = sonos_loop.state()
    return jsonify(snapshot)


def whole_seconds(name):
    try:
        return int(request.values.get(name, ""))
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be whole seconds") from None


@bp.route("/api/speakers/seek", methods=["POST"])
def api_speaker_seek():
    try:
        offset = whole_seconds("offset")
        if not 0 < abs(offset) <= MAX_SEEK_OFFSET:
            raise ValueError(f"offset must be 1 to {MAX_SEEK_OFFSET} seconds either way")
    except ValueError as exc:
        return api_error(exc, 400)
    try:
        position = sonos.seek(offset)
    except SonosNotSeekable as exc:
        return api_error(exc, 409)
    except Exception as exc:
        return api_error(f"Sonos seek failed: {exc}", 502)
    return jsonify(
        {"ok": True, "message": f"Sonos at {clock_label(position)}", "position": position}
    )


@bp.route("/api/speakers/loop", methods=["POST"])
def api_speaker_loop():
    try:
        start = whole_seconds("start")
        end = whole_seconds("end")
    except ValueError as exc:
        return api_error(exc, 400)
    try:
        message = sonos_loop.start(start, end)
    except ValueError as exc:
        return api_error(exc, 400)
    except SonosNotSeekable as exc:
        return api_error(exc, 409)
    except Exception as exc:
        return api_error(f"could not start the Sonos loop: {exc}", 502)
    return jsonify({"ok": True, "message": message, "loop": sonos_loop.state()})


@bp.route("/api/speakers/loop/stop", methods=["POST"])
def api_speaker_loop_stop():
    message = sonos_loop.stop()
    return jsonify({"ok": True, "message": message, "loop": sonos_loop.state()})



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
