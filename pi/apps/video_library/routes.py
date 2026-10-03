"""HTTP route boundary for the video library."""

from __future__ import annotations

import ipaddress
import math
import re
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request

if __package__:
    from .catalog import CatalogConflict
    from .media_models import seconds_text
    from .players.sonos_volume import AudioPreparingError
    from .players.vlc_player import RoomPreparationError
    from .service import active_service
    from .video_qbittorrent import QbittorrentError
else:  # Direct execution from the Pi's flat deployment directory.
    from catalog import CatalogConflict  # type: ignore[no-redef]
    from media_models import seconds_text  # type: ignore[no-redef]
    from sonos_volume import AudioPreparingError  # type: ignore[no-redef]
    from vlc_player import RoomPreparationError  # type: ignore[no-redef]
    from service import active_service  # type: ignore[no-redef]
    from video_qbittorrent import QbittorrentError  # type: ignore[no-redef]


app = Flask(__name__)








def api_error(message: Any, status: int):
    return jsonify({"ok": False, "message": str(message)}), status


@app.before_request
def reject_cross_origin_mutations():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    if origin:
        if urlsplit(origin).netloc != request.host:
            return api_error("cross-origin control request rejected", 403)
        if request.headers.get("X-Van-Video") != "1":
            return api_error("video control header missing", 403)
    elif referer and urlsplit(referer).netloc != request.host:
        return api_error("cross-origin control request rejected", 403)
    return None


@app.after_request
def disable_api_cache(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/status")
def api_status():
    return jsonify(active_service().status())


@app.route("/api/library")
def api_library():
    return jsonify(active_service().library_payload())


@app.route("/api/search")
def api_search():
    query = request.args.get("q", "").strip()
    return jsonify({"ok": True, "q": query, "matches": active_service().search(query)})


@app.route("/api/shows/<show_id>")
def api_show(show_id: str):
    try:
        return jsonify(active_service().show_payload(show_id))
    except KeyError as exc:
        return api_error(exc.args[0], 404)


@app.route("/api/rescan", methods=["POST"])
def api_rescan():
    service = active_service()
    available = service.rescan()
    payload = service.library_payload()
    payload["message"] = "Library rescanned" if available else service.library.error
    return jsonify(payload), 200 if available else 503


def form_boolean(name: str, default: bool = False) -> bool:
    raw = request.form.get(name)
    if raw is None:
        return default
    if raw.casefold() not in ("1", "0", "true", "false", "on", "off"):
        raise ValueError(f"{name} must be true or false")
    return raw.casefold() in ("1", "true", "on")


@app.route("/api/play", methods=["POST"])
def api_play():
    try:
        result = active_service().play(
            item_id=request.form.get("item") or None,
            show_id=request.form.get("show") or None,
            query=request.form.get("q") or None,
            restart=form_boolean("restart"),
            shuffle=form_boolean("shuffle"),
            subtitles=request.form.get("subtitles", "auto"),
        )
        return jsonify(result)
    except KeyError as exc:
        return api_error(exc.args[0], 404)
    except ValueError as exc:
        return api_error(exc, 400)
    except RuntimeError as exc:
        return api_error(exc, 503)
    except Exception as exc:
        return api_error(f"playback failed: {exc}", 502)


def require_loopback_peer():
    try:
        peer = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return api_error("local media controls require a loopback connection", 403)
    if not peer.is_loopback:
        return api_error("local media controls require a loopback connection", 403)
    return None


@app.route("/api/play-local", methods=["POST"])
def api_play_local():
    rejected = require_loopback_peer()
    if rejected is not None:
        return rejected
    try:
        return jsonify(
            active_service().play_local(
                request.form.get("path", ""),
                restart=form_boolean("restart"),
                subtitles=request.form.get("subtitles", "auto"),
            )
        )
    except FileNotFoundError as exc:
        return api_error(exc, 404)
    except ValueError as exc:
        return api_error(exc, 400)
    except RuntimeError as exc:
        return api_error(exc, 503)
    except Exception as exc:
        return api_error(f"local playback failed: {exc}", 502)


@app.route("/api/torrents/reconcile", methods=["POST"])
def api_torrent_reconcile():
    rejected = require_loopback_peer()
    if rejected is not None:
        return rejected
    torrent_id = request.form.get("torrent", "").strip() or None
    if torrent_id is not None and not re.fullmatch(r"[0-9a-fA-F]{40}", torrent_id):
        return api_error("torrent must be a 40-character qBittorrent ID", 400)
    try:
        return jsonify(active_service().reconcile_torrents(torrent_id))
    except QbittorrentError as exc:
        return api_error(f"qBittorrent reconciliation failed: {exc}", 503)
    except CatalogConflict as exc:
        return api_error(f"media identity conflict: {exc}", 409)
    except Exception as exc:
        return api_error(f"torrent reconciliation failed: {exc}", 502)


@app.route("/api/surprise", methods=["POST"])
def api_surprise():
    try:
        return jsonify(
            active_service().surprise(
                request.form.get("type", "any"),
                request.form.get("subtitles", "auto"),
            )
        )
    except ValueError as exc:
        return api_error(exc, 400)
    except RuntimeError as exc:
        return api_error(exc, 503)
    except Exception as exc:
        return api_error(f"playback failed: {exc}", 502)


@app.route("/api/control", methods=["POST"])
def api_control():
    action = request.form.get("action", "")
    if action not in ("toggle", "play", "pause", "next", "previous", "stop"):
        return api_error("unknown control action", 400)
    try:
        return jsonify(active_service().control_player(action))
    except RoomPreparationError as exc:
        return api_error(f"rear movie audio setup failed: {exc}", 503)
    except Exception as exc:
        return api_error(f"VLC control failed: {exc}", 502)


@app.route("/api/seek", methods=["POST"])
def api_seek():
    try:
        seconds = float(request.form.get("seconds", ""))
        if not math.isfinite(seconds) or abs(seconds) > 3600:
            raise ValueError
    except (TypeError, ValueError):
        return api_error("seconds must be between -3600 and 3600", 400)
    try:
        active_service().player.seek(seconds)
        return jsonify({"ok": True, "message": f"Seek {seconds:+g} seconds"})
    except Exception as exc:
        return api_error(f"VLC seek failed: {exc}", 502)


@app.route("/api/position", methods=["POST"])
def api_position():
    try:
        position = float(request.form.get("position", ""))
        if not math.isfinite(position) or position < 0:
            raise ValueError
    except (TypeError, ValueError):
        return api_error("position must be a non-negative number of seconds", 400)

    service = active_service()
    snapshot = service.player.snapshot()
    try:
        duration = float(snapshot.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    if (
        not snapshot.get("available")
        or snapshot.get("track_id") is None
        or not snapshot.get("can_seek")
        or not math.isfinite(duration)
        or duration <= 0
    ):
        return api_error("the current VLC track is not seekable", 409)
    if position > duration:
        return api_error(f"position must be between 0 and {duration:g}", 400)
    try:
        service.player.set_position(snapshot["track_id"], position)
        return jsonify(
            {
                "ok": True,
                "position": position,
                "message": f"Jumped to {seconds_text(position)}",
            }
        )
    except Exception as exc:
        return api_error(f"VLC position change failed: {exc}", 502)


@app.route("/api/volume", methods=["POST"])
def api_volume():
    raw = request.form.get("value", "").strip()
    try:
        if not re.fullmatch(r"\d{1,3}", raw):
            raise ValueError
        volume = int(raw)
        if not 0 <= volume <= 100:
            raise ValueError
    except (TypeError, ValueError):
        return api_error("Sonos volume must be a whole number from 0 to 100", 400)
    try:
        audio = active_service().set_audio_volume(volume)
        return jsonify(
            {
                "ok": True,
                "audio": audio,
                "volume": audio["volume"],
                "message": f"{audio['device']} Sonos volume {audio['volume']}%",
            }
        )
    except AudioPreparingError as exc:
        return api_error(exc, 409)
    except Exception as exc:
        return api_error(f"Sonos volume failed: {exc}", 502)


@app.route("/api/rate", methods=["POST"])
def api_rate():
    try:
        value = float(request.form.get("value", ""))
        if not math.isfinite(value) or not 0.5 <= value <= 2:
            raise ValueError
    except (TypeError, ValueError):
        return api_error("rate must be from 0.5 to 2", 400)
    try:
        actual = active_service().player.set_rate(value)
        return jsonify({"ok": True, "rate": actual, "message": f"Playback speed {actual:g}×"})
    except Exception as exc:
        return api_error(f"VLC speed failed: {exc}", 502)


@app.route("/api/fullscreen", methods=["POST"])
def api_fullscreen():
    try:
        value = active_service().player.toggle_fullscreen()
        return jsonify({"ok": True, "fullscreen": value, "message": "Fullscreen on" if value else "Fullscreen off"})
    except RuntimeError as exc:
        return api_error(exc, 409)
    except Exception as exc:
        return api_error(f"fullscreen control failed: {exc}", 502)


@app.route("/api/sleep", methods=["POST"])
def api_sleep():
    try:
        minutes = int(request.form.get("minutes", ""))
    except (TypeError, ValueError):
        return api_error("sleep timer must be a whole number of minutes", 400)
    try:
        return jsonify(active_service().set_sleep_timer(minutes))
    except ValueError as exc:
        return api_error(exc, 400)


@app.route("/api/progress", methods=["POST"])
def api_progress():
    item_id = request.form.get("item", "")
    action = request.form.get("action", "")
    service = active_service()
    item = service.library.items.get(item_id)
    if not item:
        return api_error("unknown media id", 404)
    if action == "clear":
        changed = service.update_progress(item, action)
        message = "Progress cleared" if changed else "Progress was already clear"
    elif action == "watched":
        service.update_progress(item, action)
        message = "Marked watched"
    elif action == "unwatched":
        service.update_progress(item, action)
        message = "Marked unwatched"
    else:
        return api_error("action must be clear, watched, or unwatched", 400)
    return jsonify({"ok": True, "message": message})


APP_ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" rx="112" fill="#111820"/>
<rect x="78" y="116" width="356" height="280" rx="40" fill="#263746" stroke="#8ed3c7" stroke-width="18"/>
<path d="M230 194 338 256 230 318Z" fill="#f6c76d"/>
<path d="M133 92v48M205 92v48M307 92v48M379 92v48" stroke="#f6c76d" stroke-width="18" stroke-linecap="round"/>
</svg>"""


@app.route("/manifest.webmanifest")
def manifest():
    response = jsonify(
        {
            "name": "Van Movies & TV",
            "short_name": "Movies & TV",
            "id": "/",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#0b1117",
            "theme_color": "#111820",
            "icons": [{"src": "/app-icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        }
    )
    response.mimetype = "application/manifest+json"
    return response


@app.route("/app-icon.svg")
def app_icon():
    response = app.response_class(APP_ICON, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.route("/")
def index():
    return render_template("video_library.html")
