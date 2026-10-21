#!/usr/bin/env python3
# Audiobook server: fuzzy-matched playback of /mnt/EXFAT512/Audiobooks_Radio
# on Sonos, with per-book resume (~60s rewind) and a phone-friendly web UI.
#
# Runs as systemd unit `audiobooks.service` on port 8787.
#   API:   GET /api/play?q=3+body+ptoblem   (also /api/toggle /api/skip?s=-30 ...)
#   Audio: GET /audio/<book_id>/<track>.<ext>   (served to the Sonos with Range support)
#   UI:    GET /                               (add to iPhone home screen)

import json
import os
from pathlib import Path
import re
import socket
import threading
import time
from urllib.parse import quote, unquote

from flask import Flask, abort, jsonify, request, send_file
from soco import SoCo
from soco.discovery import discover
from .catalog import AUDIO_EXTS, DISC_RE, Book, audio_files, match_score, natural_key, norm_tokens

LIBRARY = "/mnt/EXFAT512/Audiobooks_Radio"
STATE_PATH = os.path.expanduser("~/.audiobook_state.json")
PORT = 8787
DEFAULT_DEVICE = "vonFront"
RESUME_REWIND = 60          # start this many seconds before the last bookmark
POLL_SECS = 10              # progress poller interval
RESCAN_SECS = 3600          # background library rescan interval
MIN_MATCH_SCORE = 0.52

MIME = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".m4b": "audio/mp4",
    ".aac": "audio/aac", ".flac": "audio/flac", ".ogg": "audio/ogg",
    ".wav": "audio/wav", ".aiff": "audio/aiff",
}
URI_RE = re.compile(r"/audio/([0-9a-f]{12})/(\d+)\.")

app = Flask(__name__)

# ---------------------------------------------------------------- library ---

books = {}          # bid -> Book
books_lock = threading.Lock()
last_scan = 0.0

def scan_library():
    global books, last_scan
    found = {}
    try:
        entries = sorted(os.listdir(LIBRARY), key=natural_key)
    except OSError as e:
        print(f"scan: library unavailable: {e}", flush=True)
        return False
    for entry in entries:
        if entry.startswith("."):
            continue
        path = os.path.join(LIBRARY, entry)
        if os.path.isfile(path):
            if os.path.splitext(entry)[1].lower() in AUDIO_EXTS:
                b = Book(entry, [path])
                found[b.bid] = b
        elif os.path.isdir(path):
            tracks = audio_files(path)
            if not tracks:
                continue
            b = Book(entry, tracks)
            found[b.bid] = b
            # index non-disc subdirs as their own titles (e.g. HP "Book 4 - ...")
            for sub in sorted(os.listdir(path), key=natural_key):
                subpath = os.path.join(path, sub)
                if (os.path.isdir(subpath) and not sub.startswith(".")
                        and not DISC_RE.match(sub.strip())):
                    subtracks = audio_files(subpath)
                    if subtracks:
                        sb = Book(f"{entry}/{sub}", subtracks)
                        found[sb.bid] = sb
    with books_lock:
        books = found
    last_scan = time.time()
    print(f"scan: {len(found)} playable titles", flush=True)
    return True

def maybe_rescan():
    if time.time() - last_scan > RESCAN_SECS:
        scan_library()

# ------------------------------------------------------------ fuzzy match ---

def search_books(query, limit=8):
    qtok = norm_tokens(query)
    with books_lock:
        candidates = list(books.values())
    scored = [(match_score(qtok, b.tokens), b) for b in candidates]
    scored.sort(key=lambda t: (-t[0], len(t[1].rel)))
    return [(s, b) for s, b in scored[:limit] if s >= MIN_MATCH_SCORE]

# ------------------------------------------------------------------ state ---

state_lock = threading.RLock()

def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"books": {}, "device": None}

state = load_state()
state.setdefault("books", {})

def save_state():
    with state_lock:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=1)
        os.replace(tmp, STATE_PATH)

def hms_to_secs(hms):
    try:
        parts = [int(p) for p in hms.split(":")]
    except (AttributeError, ValueError):
        return None
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]

def secs_to_hms(secs):
    secs = max(0, int(secs))
    return "%d:%02d:%02d" % (secs // 3600, secs % 3600 // 60, secs % 60)

# ------------------------------------------------------------------ sonos ---

zones = {}          # player_name -> SoCo
zones_ts = 0.0
zones_lock = threading.Lock()

def get_zones(force=False):
    global zones, zones_ts
    with zones_lock:
        if zones and not force and time.time() - zones_ts < 600:
            return zones
        found = discover(timeout=5) or set()
        fresh = {z.player_name: z for z in found if z.is_visible}
        if fresh:
            zones = fresh
            zones_ts = time.time()
        return zones

def get_device(name=None):
    """Resolve the group coordinator to control: named > remembered > playing > default."""
    zmap = get_zones()
    if not zmap:
        zmap = get_zones(force=True)
        if not zmap:
            raise RuntimeError("no Sonos speakers found")
    with state_lock:
        remembered_device = state.get("device")
    for candidate in (name, remembered_device):
        if candidate and candidate in zmap:
            return zmap[candidate].group.coordinator
    paused = None
    for z in zmap.values():
        if z.group.coordinator != z:
            continue
        try:
            t = z.get_current_transport_info()["current_transport_state"]
        except Exception:
            continue
        if t == "PLAYING":
            return z
        if t == "PAUSED_PLAYBACK" and not paused:
            paused = z
    if paused:
        return paused
    if DEFAULT_DEVICE in zmap:
        return zmap[DEFAULT_DEVICE].group.coordinator
    return next(iter(zmap.values())).group.coordinator

def local_ip_for(peer_ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((peer_ip, 1400))
        return s.getsockname()[0]
    finally:
        s.close()

def track_uri(host_ip, book, idx):
    ext = os.path.splitext(book.tracks[idx])[1].lower()
    ext = ".m4a" if ext == ".m4b" else ext    # Sonos rejects the .m4b extension
    return f"http://{host_ip}:{PORT}/audio/{book.bid}/{idx}{ext}"

def parse_our_uri(uri):
    m = URI_RE.search(uri or "")
    return (m.group(1), int(m.group(2))) if m else (None, None)

enqueue_gen = 0     # bumped on every new play to cancel stale background enqueues

def start_book(book, device, from_track, from_secs):
    global enqueue_gen
    enqueue_gen += 1
    gen = enqueue_gen
    host_ip = local_ip_for(device.ip_address)
    device.stop()
    device.play_mode = "NORMAL"
    device.clear_queue()
    device.add_uri_to_queue(track_uri(host_ip, book, from_track))
    device.play_from_queue(0)
    if from_secs > 0:
        deadline = time.time() + 8
        while time.time() < deadline:
            t = device.get_current_transport_info()["current_transport_state"]
            if t == "PLAYING":
                break
            time.sleep(0.4)
        device.seek(secs_to_hms(from_secs))

    def enqueue_rest():
        for idx in range(from_track + 1, len(book.tracks)):
            if enqueue_gen != gen:
                return
            try:
                device.add_uri_to_queue(track_uri(host_ip, book, idx))
            except Exception as e:
                print(f"enqueue: track {idx} failed: {e}", flush=True)
                return
    if from_track + 1 < len(book.tracks):
        threading.Thread(target=enqueue_rest, daemon=True).start()

# ------------------------------------------------------- progress tracker ---

def poll_progress():
    with state_lock:
        devname = state.get("device")
    zmap = get_zones()
    if not devname or devname not in zmap:
        return
    dev = zmap[devname].group.coordinator
    t = dev.get_current_transport_info()["current_transport_state"]
    if t not in ("PLAYING", "PAUSED_PLAYBACK"):
        return
    info = dev.get_current_track_info()
    bid, idx = parse_our_uri(info.get("uri"))
    if bid is None:
        return
    with books_lock:
        book = books.get(bid)
    if not book or idx >= len(book.tracks):
        return
    pos = hms_to_secs(info.get("position"))
    dur = hms_to_secs(info.get("duration"))
    if pos is None:
        return
    with state_lock:
        entry = state["books"].get(book.rel, {})
        entry.update({
            "id": bid, "track": idx, "pos": pos, "duration": dur or 0,
            "tracks_total": len(book.tracks), "updated": int(time.time()),
            "finished": bool(idx == len(book.tracks) - 1 and dur and pos >= dur - 20),
        })
        state["books"][book.rel] = entry
        state["last_book"] = book.rel
        save_state()

def poller_loop():
    while True:
        time.sleep(POLL_SECS)
        try:
            poll_progress()
        except Exception as e:
            print(f"poller: {e}", flush=True)
            get_zones(force=True)

def bookmark_now():
    try:
        poll_progress()
    except Exception:
        pass

# -------------------------------------------------------------------- api ---

def book_progress(book):
    with state_lock:
        entry = state["books"].get(book.rel)
        if not entry:
            return None
        return {"track": entry["track"],
                "tracks_total": entry.get("tracks_total", len(book.tracks)),
                "pos": entry["pos"], "pos_hms": secs_to_hms(entry["pos"]),
                "duration": entry.get("duration", 0), "updated": entry.get("updated"),
                "finished": entry.get("finished", False)}

@app.route("/api/books")
def api_books():
    maybe_rescan()
    with books_lock:
        blist = list(books.values())
    out = [b.as_dict(book_progress(b)) for b in blist]
    out.sort(key=lambda d: (-(d["progress"]["updated"] or 0) if d.get("progress") else 0,
                            d["name"].lower()))
    return jsonify({"library": LIBRARY, "count": len(out), "books": out})

@app.route("/api/search")
def api_search():
    q = request.args.get("q", "")
    matches = search_books(q)
    return jsonify({"q": q, "matches": [
        dict(b.as_dict(book_progress(b)), score=round(s, 3)) for s, b in matches]})

@app.route("/api/progress/clear", methods=["POST"])
def api_clear_progress():
    bid = request.values.get("book", "").strip()
    with books_lock:
        book = books.get(bid)
    if not book:
        return jsonify({"ok": False, "message": "unknown book id"}), 404
    with state_lock:
        removed = state["books"].pop(book.rel, None)
        if state.get("last_book") == book.rel:
            state.pop("last_book", None)
        save_state()
    message = f"cleared progress for '{book.name}'" if removed else "progress already clear"
    return jsonify({"ok": True, "book": bid, "message": message})

@app.route("/api/play", methods=["GET", "POST"])
def api_play():
    q = request.values.get("q", "").strip()
    bid = request.values.get("book")
    restart = request.values.get("restart") in ("1", "true")
    devname = request.values.get("device")

    maybe_rescan()
    if bid:
        with books_lock:
            book = books.get(bid)
        if not book:
            return jsonify({"ok": False, "message": "unknown book id"}), 404
    else:
        if not q:
            return jsonify({"ok": False, "message": "need q= or book="}), 400
        matches = search_books(q)
        if not matches:
            scan_library()
            matches = search_books(q)
        if not matches:
            return jsonify({"ok": False, "message": f"no match for '{q}'"}), 404
        book = matches[0][1]

    bookmark_now()                      # bookmark whatever was playing before
    try:
        device = get_device(devname)
    except RuntimeError as e:
        return jsonify({"ok": False, "message": str(e)}), 503

    with state_lock:
        entry = state["books"].get(book.rel)
        entry = entry.copy() if entry else None
        if restart:
            state["books"].pop(book.rel, None)
    from_track, from_secs = 0, 0
    if entry and not restart and not entry.get("finished"):
        from_track = min(entry.get("track", 0), len(book.tracks) - 1)
        from_secs = max(0, entry.get("pos", 0) - RESUME_REWIND)
    try:
        start_book(book, device, from_track, from_secs)
    except Exception as e:
        return jsonify({"ok": False, "message": f"playback failed: {e}"}), 502

    with state_lock:
        state["device"] = device.player_name
        state["last_book"] = book.rel
        save_state()
    verb = "Resuming" if (from_track or from_secs) else "Playing"
    msg = (f"{verb} '{book.name}' ch {from_track + 1}/{len(book.tracks)}"
           f" at {secs_to_hms(from_secs)} on {device.player_name}")
    print(msg, flush=True)
    return jsonify({"ok": True, "book": book.as_dict(), "track": from_track,
                    "pos": from_secs, "device": device.player_name, "message": msg})

def control_device():
    with state_lock:
        devname = state.get("device")
    zmap = get_zones()
    if devname and devname in zmap:
        return zmap[devname].group.coordinator
    return get_device()


def control_device_response():
    """Return the selected coordinator or the API's established unavailable response."""
    try:
        return control_device(), None
    except RuntimeError as error:
        return None, (jsonify({"ok": False, "message": str(error)}), 503)

def speaker_snapshot(force=False):
    zmap = get_zones(force=force)
    if not zmap:
        raise RuntimeError("no Sonos speakers found")
    with state_lock:
        devname = state.get("device")
    if devname and devname in zmap:
        coordinator = zmap[devname].group.coordinator
    else:
        coordinator = get_device()
    coordinator_name = coordinator.player_name
    member_names = {member.player_name for member in coordinator.group.members}
    speakers = []
    for name, zone in sorted(zmap.items()):
        try:
            volume = zone.volume
        except Exception:
            volume = None
        speakers.append({
            "name": name, "volume": volume, "grouped": name in member_names,
            "coordinator": name == coordinator_name,
            "group_coordinator": zone.group.coordinator.player_name,
        })
    return {"ok": True, "coordinator": coordinator_name, "speakers": speakers}

@app.route("/api/device", methods=["GET", "POST"])
def api_device():
    name = request.values.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "message": "need name="}), 400
    zmap = get_zones()
    if name not in zmap:
        return jsonify({"ok": False, "message": f"unknown device '{name}'"}), 404
    coordinator = zmap[name].group.coordinator
    with state_lock:
        state["device"] = coordinator.player_name
        save_state()
    return jsonify({"ok": True, "device": coordinator.player_name,
                    "message": f"using {coordinator.player_name}"})

@app.route("/api/speakers")
def api_speakers():
    try:
        return jsonify(speaker_snapshot())
    except Exception as e:
        return jsonify({"ok": False, "message": f"speaker discovery failed: {e}"}), 503

@app.route("/api/speakers/group", methods=["POST"])
def api_speaker_group():
    name = request.values.get("name", "").strip()
    grouped = request.values.get("grouped", "").lower() in ("1", "true", "yes")
    zmap = get_zones()
    if name not in zmap:
        return jsonify({"ok": False, "message": f"unknown device '{name}'"}), 404
    coordinator = control_device()
    speaker = zmap[name]
    if not grouped and speaker.player_name == coordinator.player_name:
        return jsonify({"ok": False,
                        "message": "select another group before removing its coordinator"}), 400
    try:
        active_members = {member.player_name for member in coordinator.group.members}
        if grouped and name not in active_members:
            speaker.join(coordinator)
            message = f"added {name} to {coordinator.player_name}"
        elif not grouped and name in active_members:
            speaker.unjoin()
            message = f"removed {name} from {coordinator.player_name}"
        else:
            message = f"{name} group is unchanged"
        get_zones(force=True)
    except Exception as e:
        return jsonify({"ok": False, "message": f"could not update Sonos group: {e}"}), 502
    return jsonify({"ok": True, "message": message})

@app.route("/api/speakers/volume", methods=["POST"])
def api_speaker_volume():
    name = request.values.get("name", "").strip()
    zmap = get_zones()
    if name not in zmap:
        return jsonify({"ok": False, "message": f"unknown device '{name}'"}), 404
    try:
        volume = max(0, min(100, int(request.values.get("volume", ""))))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "message": "volume must be from 0 to 100"}), 400
    try:
        zmap[name].volume = volume
    except Exception as e:
        return jsonify({"ok": False, "message": f"could not set {name} volume: {e}"}), 502
    return jsonify({"ok": True, "device": name, "volume": volume,
                    "message": f"{name} volume: {volume}"})

@app.route("/api/toggle", methods=["GET", "POST"])
def api_toggle():
    dev, error = control_device_response()
    if error:
        return error
    t = dev.get_current_transport_info()["current_transport_state"]
    if t == "PLAYING":
        bookmark_now()
        dev.pause()
        action = "paused"
    else:
        dev.play()
        action = "playing"
    return jsonify({"ok": True, "message": action, "device": dev.player_name})

@app.route("/api/pause", methods=["GET", "POST"])
def api_pause():
    bookmark_now()
    dev, error = control_device_response()
    if error:
        return error
    try:
        dev.pause()
    except Exception:
        pass
    return jsonify({"ok": True, "message": "paused"})

@app.route("/api/skip", methods=["GET", "POST"])
def api_skip():
    delta = int(request.values.get("s", "-30"))
    dev, error = control_device_response()
    if error:
        return error
    pos = hms_to_secs(dev.get_current_track_info().get("position")) or 0
    dev.seek(secs_to_hms(pos + delta))
    bookmark_now()
    return jsonify({"ok": True, "message": f"skipped {delta:+d}s"})

@app.route("/api/chapter", methods=["GET", "POST"])
def api_chapter():
    d = int(request.values.get("d", "1"))
    dev, error = control_device_response()
    if error:
        return error
    bid, idx = parse_our_uri(dev.get_current_track_info().get("uri"))
    if bid is None:
        return jsonify({"ok": False, "message": "not playing an audiobook"}), 409
    with books_lock:
        book = books.get(bid)
    if not book:
        return jsonify({"ok": False, "message": "book not in library"}), 409
    new_idx = max(0, min(idx + d, len(book.tracks) - 1))
    start_book(book, dev, new_idx, 0)
    bookmark_now()
    return jsonify({"ok": True, "message": f"chapter {new_idx + 1}/{len(book.tracks)}"})

@app.route("/api/volume", methods=["GET", "POST"])
def api_volume():
    dev, error = control_device_response()
    if error:
        return error
    setval = request.values.get("set")
    delta = int(request.values.get("d", "0"))
    for member in dev.group.members:
        if setval is not None:
            member.volume = max(0, min(100, int(setval)))
        elif delta:
            member.volume = max(0, min(100, member.volume + delta))
    return jsonify({"ok": True, "volume": dev.volume})

@app.route("/api/status")
def api_status():
    with state_lock:
        out = {"ok": True, "device": state.get("device"), "playing": None,
               "last_book": state.get("last_book")}
    try:
        dev = control_device()
        t = dev.get_current_transport_info()["current_transport_state"]
        info = dev.get_current_track_info()
        bid, idx = parse_our_uri(info.get("uri"))
        with books_lock:
            book = books.get(bid) if bid else None
        out.update({
            "device": dev.player_name, "transport": t, "volume": dev.volume,
            "devices": sorted(get_zones().keys()),
        })
        if book:
            out["playing"] = {
                "id": book.bid, "name": book.name, "track": idx,
                "tracks_total": len(book.tracks),
                "chapter": os.path.splitext(os.path.basename(book.tracks[idx]))[0]
                           if idx < len(book.tracks) else "",
                "position": info.get("position"), "duration": info.get("duration"),
                "state": t,
            }
    except Exception as e:
        out["ok"] = False
        out["message"] = str(e)
    return jsonify(out)

@app.route("/api/rescan", methods=["GET", "POST"])
def api_rescan():
    available = scan_library()
    with books_lock:
        n = len(books)
    if not available:
        return jsonify({"ok": False, "count": n,
                        "message": "audiobook drive is unavailable"}), 503
    return jsonify({"ok": True, "count": n,
                    "message": f"library refreshed: {n} titles"})

# ------------------------------------------------------------ file server ---

@app.route("/audio/<bid>/<fname>")
def serve_audio(bid, fname):
    m = re.match(r"(\d+)\.\w+$", fname)
    if not m:
        abort(404)
    idx = int(m.group(1))
    with books_lock:
        book = books.get(bid)
    if not book or idx >= len(book.tracks):
        abort(404)
    path = book.tracks[idx]
    ext = os.path.splitext(path)[1].lower()
    return send_file(path, mimetype=MIME.get(ext, "application/octet-stream"),
                     conditional=True)

# --------------------------------------------------------------------- ui ---

PAGE = (Path(__file__).with_name("templates") / "audiobooks.html").read_text(encoding="utf-8")

APP_ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" rx="112" fill="#211d19"/>
<path d="M103 136c54-16 105-5 153 31v244c-48-36-99-46-153-30V136Z" fill="#eea94b"/>
<path d="M409 136c-54-16-105-5-153 31v244c48-36 99-46 153-30V136Z" fill="#c9792d"/>
<path d="M256 167v244" stroke="#161310" stroke-width="18"/>
<path d="M143 205c29-4 54 1 76 15M143 259c29-4 54 1 76 15M369 205c-29-4-54 1-76 15M369 259c-29-4-54 1-76 15" stroke="#211d19" stroke-width="14" stroke-linecap="round" opacity=".75"/>
</svg>"""

@app.route("/manifest.webmanifest")
def manifest():
    response = jsonify({
        "name": "Van Audiobooks", "short_name": "Audiobooks", "id": "/",
        "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#161310", "theme_color": "#161310",
        "icons": [{"src": "/app-icon.svg", "sizes": "any", "type": "image/svg+xml"}],
    })
    response.mimetype = "application/manifest+json"
    return response

@app.route("/app-icon.svg")
def app_icon():
    response = app.response_class(APP_ICON, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response

@app.route("/")
def index():
    return PAGE

# ------------------------------------------------------------------- main ---

if __name__ == "__main__":
    scan_library()
    threading.Thread(target=poller_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
