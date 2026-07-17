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
import re
import socket
import threading
import time
from difflib import SequenceMatcher
from hashlib import sha1
from urllib.parse import quote, unquote

from flask import Flask, abort, jsonify, request, send_file
from soco import SoCo
from soco.discovery import discover

LIBRARY = "/mnt/EXFAT512/Audiobooks_Radio"
STATE_PATH = os.path.expanduser("~/.audiobook_state.json")
PORT = 8787
DEFAULT_DEVICE = "vonFront"
RESUME_REWIND = 60          # start this many seconds before the last bookmark
POLL_SECS = 10              # progress poller interval
RESCAN_SECS = 3600          # background library rescan interval
MIN_MATCH_SCORE = 0.52

AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".aac", ".flac", ".ogg", ".wav", ".aiff"}
MIME = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".m4b": "audio/mp4",
    ".aac": "audio/aac", ".flac": "audio/flac", ".ogg": "audio/ogg",
    ".wav": "audio/wav", ".aiff": "audio/aiff",
}
# subdirs that are just media splits, not standalone titles
DISC_RE = re.compile(r"^(dis[ck]|cd|dvd|part|pt|side|session)[\s._-]*\d+$", re.I)
URI_RE = re.compile(r"/audio/([0-9a-f]{12})/(\d+)\.")

app = Flask(__name__)

# ---------------------------------------------------------------- library ---

NUMWORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "i": "1", "ii": "2", "iii": "3", "iv": "4",
}

def norm_tokens(text):
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[’']", "", text)
    tokens = re.sub(r"[^a-z0-9]+", " ", text).split()
    return [NUMWORDS.get(t, t) for t in tokens]

def natural_key(s):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", s)]

class Book:
    def __init__(self, rel, tracks):
        self.rel = rel                      # path relative to LIBRARY, or filename
        self.bid = sha1(rel.encode()).hexdigest()[:12]
        self.tracks = tracks                # abs paths, playback order
        self.name = re.sub(r"\.[^.]+$", "", rel.replace("/", " - "))
        self.tokens = norm_tokens(self.name)

    def as_dict(self, progress=None):
        d = {"id": self.bid, "name": self.name, "rel": self.rel,
             "tracks": len(self.tracks)}
        if progress:
            d["progress"] = progress
        return d

books = {}          # bid -> Book
books_lock = threading.Lock()
last_scan = 0.0

def audio_files(dirpath):
    found = []
    for root, dirs, files in os.walk(dirpath):
        dirs.sort(key=natural_key)
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in sorted(files, key=natural_key):
            if not f.startswith(".") and os.path.splitext(f)[1].lower() in AUDIO_EXTS:
                found.append(os.path.join(root, f))
    return found

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

def token_sim(q, c):
    if q == c:
        return 1.0
    if len(q) >= 3 and (c.startswith(q) or q.startswith(c)):
        return 0.92
    return SequenceMatcher(None, q, c).ratio()

def match_score(query_tokens, cand_tokens):
    if not query_tokens or not cand_tokens:
        return 0.0
    best = [max(token_sim(q, c) for c in cand_tokens) for q in query_tokens]
    mean_best = sum(best) / len(best)
    matched_cands = set()
    for q in query_tokens:
        for c in cand_tokens:
            if token_sim(q, c) >= 0.8:
                matched_cands.add(c)
    coverage = len(matched_cands) / len(cand_tokens)
    return 0.8 * mean_best + 0.2 * coverage

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
    dev = control_device()
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
    dev = control_device()
    try:
        dev.pause()
    except Exception:
        pass
    return jsonify({"ok": True, "message": "paused"})

@app.route("/api/skip", methods=["GET", "POST"])
def api_skip():
    delta = int(request.values.get("s", "-30"))
    dev = control_device()
    pos = hms_to_secs(dev.get_current_track_info().get("position")) or 0
    dev.seek(secs_to_hms(pos + delta))
    bookmark_now()
    return jsonify({"ok": True, "message": f"skipped {delta:+d}s"})

@app.route("/api/chapter", methods=["GET", "POST"])
def api_chapter():
    d = int(request.values.get("d", "1"))
    dev = control_device()
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
    dev = control_device()
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

PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Audiobooks">
<meta name="theme-color" content="#161310">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/app-icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/app-icon.svg">
<title>Audiobooks</title>
<style>
:root{color-scheme:dark;--bg:#161310;--panel:#211d19;--raised:#2a2520;--ink:#f3ece2;
  --dim:#a99d90;--accent:#eea94b;--accent2:#c9792d;--line:#39322b;--bad:#ef7b72;
  --good:#72c69a;--shadow:0 14px 45px #09070566}
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;background:radial-gradient(circle at 85% -5%,#4a2d1744,transparent 34%),var(--bg);
  color:var(--ink);font:16px/1.4 -apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif;
  min-height:100vh;padding:env(safe-area-inset-top) 14px calc(270px + env(safe-area-inset-bottom))}
main,header{width:min(100%,680px);margin:auto}
button,input,select{font:inherit}
button,select{-webkit-tap-highlight-color:transparent}
button{color:inherit}
header{display:flex;align-items:center;justify-content:space-between;padding:22px 2px 15px}
.eyebrow{color:var(--accent);font-size:11px;font-weight:750;letter-spacing:.16em;text-transform:uppercase}
h1{font-size:28px;line-height:1.05;letter-spacing:-.025em;margin:3px 0 0}
h2{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.12em;margin:22px 2px 9px}
.icon-btn,.small-btn,.control,.vol-btn{border:1px solid var(--line);background:var(--panel);border-radius:13px;
  min-width:46px;min-height:46px;cursor:pointer}
.icon-btn{font-size:21px;display:grid;place-items:center;box-shadow:var(--shadow)}
.icon-btn:active,.small-btn:active,.control:active,.vol-btn:active{background:var(--raised);transform:scale(.97)}
button:disabled{opacity:.38;cursor:default;transform:none!important}
.connection{display:flex;align-items:center;gap:7px;color:var(--dim);font-size:12px;margin:0 2px 12px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--bad);box-shadow:0 0 0 3px #ef7b7218}
.dot.on{background:var(--good);box-shadow:0 0 0 3px #72c69a20}
.setup{margin-bottom:12px}
.search-wrap{position:relative}
#q{width:100%;height:49px;border-radius:14px;border:1px solid var(--line);background:var(--panel);
  color:var(--ink);outline:none}
#q{padding:0 42px 0 15px;font-size:17px}
#q:focus,#speaker-button:focus{border-color:#a96b31;box-shadow:0 0 0 3px #eea94b18}
#speaker-button{height:49px;border-radius:14px;border:1px solid var(--line);background:var(--panel);padding:0 11px;
  display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:7px;text-align:left;cursor:pointer}
#speaker-label{font-size:13px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#speaker-count{font-size:11px;color:var(--accent);font-variant-numeric:tabular-nums}
#clear{position:absolute;right:5px;top:4px;width:41px;height:41px;border:0;background:transparent;
  color:var(--dim);font-size:20px;display:none}
.searching #clear{display:block}
.book{display:flex;align-items:stretch;background:linear-gradient(145deg,var(--panel),#1d1a17);
  border:1px solid var(--line);border-radius:16px;margin-bottom:9px;overflow:hidden;box-shadow:0 5px 18px #08060424}
.book-main{border:0;background:transparent;text-align:left;color:inherit;padding:13px 14px;min-width:0;flex:1;cursor:pointer}
.book-main:active{background:#ffffff08}
.book-name{font-size:15px;font-weight:650;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;
  -webkit-line-clamp:2;-webkit-box-orient:vertical}
.book-meta{font-size:12px;color:var(--dim);margin-top:4px}
.book-progress{color:var(--accent);margin-right:7px}
.score{color:#c5b8aa}
.restart,.clear-progress{width:52px;border:0;border-left:1px solid var(--line);background:transparent;color:var(--dim);
  font-size:21px;cursor:pointer}
.restart:active,.clear-progress:active{background:#ffffff08;color:var(--accent)}
.empty{border:1px dashed var(--line);border-radius:16px;color:var(--dim);padding:28px 18px;text-align:center}
.library-count{float:right;color:#746a60;font-weight:500;letter-spacing:0;text-transform:none}
.speaker-backdrop{position:fixed;z-index:20;inset:0;background:#080604aa;display:flex;align-items:flex-end;
  opacity:0;pointer-events:none;transition:opacity .2s}
.speaker-backdrop.open{opacity:1;pointer-events:auto}
.speaker-sheet{width:min(100%,680px);max-height:min(78vh,680px);overflow:auto;margin:0 auto;background:#1c1815;
  border:1px solid #493d33;border-bottom:0;border-radius:22px 22px 0 0;padding:8px 14px calc(18px + env(safe-area-inset-bottom));
  box-shadow:0 -18px 60px #000a;transform:translateY(24px);transition:transform .2s}
.speaker-backdrop.open .speaker-sheet{transform:translateY(0)}
.sheet-grabber{width:38px;height:4px;border-radius:3px;background:#5b5047;margin:2px auto 12px}
.sheet-head{display:flex;align-items:center;justify-content:space-between;gap:12px}
.sheet-head h2{margin:0;color:var(--ink);font-size:18px;letter-spacing:-.01em;text-transform:none}
.sheet-close{border:1px solid var(--line);background:var(--raised);border-radius:50%;width:38px;height:38px;font-size:20px}
.sheet-help{color:var(--dim);font-size:12px;margin:3px 0 14px}
.speaker-row{display:grid;grid-template-columns:30px minmax(0,1fr) 35px;align-items:center;column-gap:8px;
  padding:11px 8px;border-top:1px solid var(--line)}
.speaker-check{width:22px;height:22px;margin:0;accent-color:var(--accent)}
.speaker-name{border:0;background:transparent;padding:2px 0;text-align:left;font-weight:700;min-width:0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}
.speaker-name small{display:block;color:var(--dim);font-size:10px;font-weight:500;margin-top:1px}
.speaker-level{font-size:11px;color:var(--dim);text-align:right;font-variant-numeric:tabular-nums}
.speaker-volume{grid-column:2/4;width:100%;margin:8px 0 0;accent-color:var(--accent)}
.speaker-loading{padding:28px;text-align:center;color:var(--dim)}
body.sheet-open{overflow:hidden}
#player{position:fixed;z-index:5;left:0;right:0;bottom:0;background:#191512f2;
  border-top:1px solid #43382f;backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
  padding:12px 14px calc(11px + env(safe-area-inset-bottom));box-shadow:0 -15px 50px #09060488}
.player-inner{width:min(100%,680px);margin:auto}
.now{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:8px}
#np-name{font-size:14px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#np-meta{font-size:11px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
#transport{color:var(--accent);font-size:11px;font-weight:750;text-transform:uppercase;letter-spacing:.1em;padding-top:2px}
#progress{height:4px;background:var(--line);border-radius:5px;overflow:hidden;margin:0 0 10px}
#progress-fill{height:100%;width:0;background:linear-gradient(90deg,var(--accent2),var(--accent));transition:width .3s}
.controls{display:grid;grid-template-columns:repeat(5,1fr);gap:7px}
.control{height:48px;min-width:0;font-size:14px;font-weight:650}
.control.primary{font-size:21px;color:var(--accent);background:#30261d;border-color:#59422e}
.subcontrols{display:grid;grid-template-columns:1fr 1fr 1fr;gap:7px;margin-top:7px}
.small-btn{min-height:35px;border-radius:10px;font-size:12px;color:var(--dim)}
.subcontrols #speaker-button{height:35px;border-radius:10px;padding:0 9px}
.subcontrols #speaker-label{font-size:12px}
.volume{display:grid;grid-template-columns:38px minmax(0,1fr) 38px 30px;align-items:center;gap:7px;margin-top:8px}
.vol-btn{min-width:38px;min-height:34px;border-radius:10px;font-size:16px}
#volume{width:100%;accent-color:var(--accent)}
#volume-value{font-size:11px;color:var(--dim);text-align:right;font-variant-numeric:tabular-nums}
#toast{position:fixed;z-index:30;left:50%;top:calc(12px + env(safe-area-inset-top));transform:translate(-50%,-14px);
  width:min(calc(100% - 28px),620px);background:#302820;color:var(--ink);border:1px solid #5a4939;
  border-radius:13px;padding:11px 14px;box-shadow:var(--shadow);opacity:0;pointer-events:none;
  transition:.2s ease;font-size:13px;text-align:center}
#toast.show{opacity:1;transform:translate(-50%,0)}
#toast.bad{border-color:#874944;color:#ffd8d4}
body.busy [data-action]{pointer-events:none;opacity:.5}
@media(hover:hover){button:hover:not(:disabled){background-image:linear-gradient(#ffffff12,#ffffff12);filter:brightness(1.12)}}
@media(max-width:420px){.control{font-size:13px}.subcontrols #speaker-button>span:first-child{display:none}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
</style></head><body>
<header>
  <div><div class="eyebrow">Van library</div><h1>Audiobooks</h1></div>
  <button class="icon-btn" id="rescan" data-action aria-label="Rescan library" title="Rescan library">↻</button>
</header>
<main>
  <div class="connection"><span class="dot" id="dot"></span><span id="connection">Connecting to vanpi…</span></div>
  <div class="setup">
    <div class="search-wrap" id="search-wrap">
      <input id="q" type="search" placeholder="Search the library" autocomplete="off" autocapitalize="none">
      <button id="clear" aria-label="Clear search">×</button>
    </div>
  </div>
  <div id="content"><div class="empty">Loading your library…</div></div>
</main>
<div class="speaker-backdrop" id="speaker-backdrop">
  <section class="speaker-sheet" id="speaker-panel" role="dialog" aria-modal="true" aria-labelledby="speaker-title">
    <div class="sheet-grabber"></div>
    <div class="sheet-head"><h2 id="speaker-title">Sonos speakers</h2><button class="sheet-close" id="speaker-close" aria-label="Close speaker selector">×</button></div>
    <p class="sheet-help">Tap a name to control its group. Check speakers to group them with the active player.</p>
    <div id="speaker-list"><div class="speaker-loading">Finding speakers…</div></div>
  </section>
</div>
<section id="player" aria-label="Playback controls">
  <div class="player-inner">
    <div class="now"><div style="min-width:0"><div id="np-name">Nothing playing</div><div id="np-meta">Choose an audiobook above</div></div><div id="transport">Idle</div></div>
    <div id="progress"><div id="progress-fill"></div></div>
    <div class="controls">
      <button class="control" data-action data-control data-api="chapter" data-key="d" data-value="-1" aria-label="Previous chapter">◀Ⅰ</button>
      <button class="control" data-action data-control data-api="skip" data-key="s" data-value="-30" aria-label="Back 30 seconds">−30</button>
      <button class="control primary" id="toggle" data-action data-control data-api="toggle" aria-label="Play or pause">▶</button>
      <button class="control" data-action data-control data-api="skip" data-key="s" data-value="30" aria-label="Forward 30 seconds">+30</button>
      <button class="control" data-action data-control data-api="chapter" data-key="d" data-value="1" aria-label="Next chapter">Ⅰ▶</button>
    </div>
    <div class="subcontrols">
      <button class="small-btn" data-action data-control data-api="skip" data-key="s" data-value="-300">−5 min</button>
      <button id="speaker-button" data-action aria-haspopup="dialog" aria-expanded="false" aria-controls="speaker-panel">
        <span aria-hidden="true">◉</span><span id="speaker-label">Speakers</span><span id="speaker-count">—</span>
      </button>
      <button class="small-btn" data-action data-control data-api="skip" data-key="s" data-value="300">+5 min</button>
    </div>
    <div class="volume">
      <button class="vol-btn" data-action data-control data-api="volume" data-key="d" data-value="-6" aria-label="Volume down">−</button>
      <input id="volume" type="range" min="0" max="100" value="0" aria-label="Volume">
      <button class="vol-btn" data-action data-control data-api="volume" data-key="d" data-value="6" aria-label="Volume up">+</button>
      <span id="volume-value">—</span>
    </div>
  </div>
</section>
<div id="toast" role="status" aria-live="polite"></div>
<script>
const $=id=>document.getElementById(id);
let all=[],status={},speakers=null,searchResults=null,searchTimer=0,searchSerial=0,busy=false,toastTimer=0;

function esc(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function seconds(value){return value?value.split(':').reduce((sum,part)=>sum*60+Number(part),0):0}
function toast(message,bad=false){
  clearTimeout(toastTimer);const el=$('toast');el.textContent=message;el.className=bad?'show bad':'show';
  toastTimer=setTimeout(()=>el.className='',3200);
}
async function json(url,options){
  const response=await fetch(url,options);let data;
  try{data=await response.json()}catch(_){data={message:`Server returned ${response.status}`}}
  if(!response.ok||data.ok===false)throw new Error(data.message||`Request failed (${response.status})`);
  return data;
}
async function post(endpoint,params={}){
  return json('/api/'+endpoint,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:new URLSearchParams(params)});
}
async function action(work,{reload=false}={}){
  if(busy)return;busy=true;document.body.classList.add('busy');
  try{const result=await work();if(result?.message)toast(result.message);if(reload)await loadLibrary();await refresh(true)}
  catch(error){toast(error.message,true)}finally{busy=false;document.body.classList.remove('busy')}
}
function bookMarkup(book,clearProgress=false){
  const progress=book.progress;let progressText='';
  if(progress?.finished)progressText='✓ Finished';
  else if(progress)progressText=`Ch ${progress.track+1}/${progress.tracks_total} · ${progress.pos_hms}`;
  const score=book.score?`<span class="score">${Math.round(book.score*100)}% match</span>`:'';
  return `<article class="book"><button class="book-main" data-action data-play="${esc(book.id)}">
    <div class="book-name">${esc(book.name)}</div><div class="book-meta">
    ${progressText?`<span class="book-progress">${esc(progressText)}</span>`:''}${score||`${book.tracks} chapters`}</div></button>
    ${clearProgress?`<button class="clear-progress" data-action data-clear="${esc(book.id)}" aria-label="Clear progress for ${esc(book.name)}">×</button>`:
      progress?`<button class="restart" data-action data-restart="${esc(book.id)}" aria-label="Restart ${esc(book.name)}">↺</button>`:''}</article>`;
}
function section(title,books,count='',clearProgress=false){
  if(!books.length)return '';
  return `<h2>${esc(title)}${count?`<span class="library-count">${esc(count)}</span>`:''}</h2>${books.map(book=>bookMarkup(book,clearProgress)).join('')}`;
}
function render(){
  const query=$('q').value.trim().toLowerCase();$('search-wrap').classList.toggle('searching',Boolean(query));
  if(query){
    const local=all.filter(book=>query.split(/\\s+/).every(token=>book.name.toLowerCase().includes(token)));
    const shown=searchResults??local;
    $('content').innerHTML=section('Search results',shown,`${shown.length} found`)||'<div class="empty">No matching audiobooks</div>';
    return;
  }
  const continuing=all.filter(book=>book.progress&&!book.progress.finished&&book.progress.updated)
    .sort((a,b)=>b.progress.updated-a.progress.updated).slice(0,5);
  const ids=new Set(continuing.map(book=>book.id));
  const library=all.filter(book=>!ids.has(book.id));
  $('content').innerHTML=section('Continue',continuing,'',true)+section('Library',library,`${all.length} titles`)
    ||'<div class="empty">The library is empty. Check the audiobook drive, then tap rescan.</div>';
}
async function loadLibrary(){
  const data=await json('/api/books');all=data.books;render();
}
async function fuzzySearch(){
  const query=$('q').value.trim();const serial=++searchSerial;
  if(!query){searchResults=null;render();return}
  try{const data=await json('/api/search?q='+encodeURIComponent(query));if(serial===searchSerial){searchResults=data.matches;render()}}
  catch(error){if(serial===searchSerial)toast(error.message,true)}
}
function renderSpeakers(next){
  speakers=next;const grouped=next.speakers.filter(speaker=>speaker.grouped);
  $('speaker-label').textContent=next.coordinator;$('speaker-count').textContent=`${grouped.length}/${next.speakers.length}`;
  $('speaker-list').innerHTML=next.speakers.map(speaker=>{
    const detail=speaker.coordinator?'Active coordinator':speaker.grouped?`Grouped with ${next.coordinator}`:`Group: ${speaker.group_coordinator}`;
    const volume=Number.isFinite(speaker.volume)?speaker.volume:0;
    return `<div class="speaker-row">
      <input class="speaker-check" data-action data-group-speaker="${esc(speaker.name)}" type="checkbox"
        ${speaker.grouped?'checked':''} ${speaker.coordinator?'disabled':''} aria-label="Group ${esc(speaker.name)}">
      <button class="speaker-name" data-action data-select-speaker="${esc(speaker.name)}">${esc(speaker.name)}<small>${esc(detail)}</small></button>
      <span class="speaker-level">${Number.isFinite(speaker.volume)?speaker.volume:'—'}</span>
      <input class="speaker-volume" data-action data-speaker-volume="${esc(speaker.name)}" type="range" min="0" max="100"
        value="${volume}" ${Number.isFinite(speaker.volume)?'':'disabled'} aria-label="${esc(speaker.name)} volume">
    </div>`;
  }).join('')||'<div class="speaker-loading">No Sonos speakers found</div>';
}
async function loadSpeakers(){
  const next=await json('/api/speakers');renderSpeakers(next);return next;
}
async function openSpeakers(){
  $('speaker-backdrop').classList.add('open');document.body.classList.add('sheet-open');
  $('speaker-button').setAttribute('aria-expanded','true');
  try{await loadSpeakers()}catch(error){$('speaker-list').innerHTML=`<div class="speaker-loading">${esc(error.message)}</div>`;toast(error.message,true)}
}
function closeSpeakers(){
  $('speaker-backdrop').classList.remove('open');document.body.classList.remove('sheet-open');
  $('speaker-button').setAttribute('aria-expanded','false');$('speaker-button').focus();
}
function updateStatus(next){
  status=next;$('dot').classList.add('on');$('connection').textContent=`Connected · ${next.device||'Sonos ready'}`;
  if(!speakers&&next.device)$('speaker-label').textContent=next.device;
  document.querySelectorAll('[data-control]').forEach(button=>button.disabled=false);
  const playing=next.playing;
  if(playing){
    $('np-name').textContent=playing.name;
    $('np-meta').textContent=`Ch ${playing.track+1}/${playing.tracks_total} · ${playing.position||'0:00'} / ${playing.duration||'—'} · ${next.device}`;
    $('transport').textContent=next.transport==='PLAYING'?'Playing':'Paused';
    $('toggle').textContent=next.transport==='PLAYING'?'Ⅱ':'▶';
    const position=seconds(playing.position),duration=seconds(playing.duration);
    $('progress-fill').style.width=(duration?Math.min(100,100*position/duration):0)+'%';
  }else{
    $('np-name').textContent='Nothing from this library is playing';$('np-meta').textContent=next.device||'Choose a speaker';
    $('transport').textContent=next.transport==='PLAYING'?'Other audio':'Idle';$('toggle').textContent='▶';$('progress-fill').style.width='0%';
  }
  if(document.activeElement!==$('volume')&&Number.isFinite(next.volume))$('volume').value=next.volume;
  $('volume-value').textContent=Number.isFinite(next.volume)?next.volume:'—';
}
function offline(message){
  $('dot').classList.remove('on');$('connection').textContent=message||'Server unavailable';
  document.querySelectorAll('[data-control]').forEach(button=>button.disabled=true);
}
async function refresh(silent=false){
  try{updateStatus(await json('/api/status'))}
  catch(error){offline(error.message);if(!silent)toast(error.message,true)}
}

$('q').addEventListener('input',()=>{
  searchResults=null;render();clearTimeout(searchTimer);searchTimer=setTimeout(fuzzySearch,220);
});
$('clear').addEventListener('click',()=>{$('q').value='';searchResults=null;++searchSerial;render();$('q').focus()});
$('speaker-button').addEventListener('click',openSpeakers);
$('speaker-close').addEventListener('click',closeSpeakers);
$('speaker-backdrop').addEventListener('click',event=>{if(event.target===$('speaker-backdrop'))closeSpeakers()});
$('rescan').addEventListener('click',()=>action(()=>post('rescan'),{reload:true}));
$('volume').addEventListener('input',()=>{$('volume-value').textContent=$('volume').value});
$('volume').addEventListener('change',()=>action(()=>post('volume',{set:$('volume').value})));
document.addEventListener('input',event=>{
  const slider=event.target.closest('[data-speaker-volume]');
  if(slider)slider.closest('.speaker-row').querySelector('.speaker-level').textContent=slider.value;
});
document.addEventListener('change',event=>{
  const checkbox=event.target.closest('[data-group-speaker]');
  if(checkbox)action(async()=>{
    try{return await post('speakers/group',{name:checkbox.dataset.groupSpeaker,grouped:checkbox.checked?'1':'0'})}
    finally{await loadSpeakers()}
  });
  const slider=event.target.closest('[data-speaker-volume]');
  if(slider)action(async()=>{
    try{return await post('speakers/volume',{name:slider.dataset.speakerVolume,volume:slider.value})}
    finally{await loadSpeakers()}
  });
});
document.addEventListener('click',event=>{
  const play=event.target.closest('[data-play]');
  if(play){action(()=>post('play',{book:play.dataset.play}));return}
  const restart=event.target.closest('[data-restart]');
  if(restart&&confirm('Start this audiobook over from the beginning?')){
    action(()=>post('play',{book:restart.dataset.restart,restart:'1'}));return}
  const clear=event.target.closest('[data-clear]');
  if(clear){action(()=>post('progress/clear',{book:clear.dataset.clear}),{reload:true});return}
  const selected=event.target.closest('[data-select-speaker]');
  if(selected){action(async()=>{const result=await post('device',{name:selected.dataset.selectSpeaker});await loadSpeakers();return result});return}
  const control=event.target.closest('[data-api]');
  if(control){const params={};if(control.dataset.key)params[control.dataset.key]=control.dataset.value;action(()=>post(control.dataset.api,params))}
});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&$('speaker-backdrop').classList.contains('open'))closeSpeakers()});
Promise.allSettled([loadLibrary(),refresh(false),loadSpeakers()]).then(results=>{
  for(const result of results)if(result.status==='rejected')toast(result.reason.message,true);
});
setInterval(()=>{if(!document.hidden)refresh(true)},5000);
setInterval(()=>{if(!document.hidden)loadLibrary().catch(error=>offline(error.message))},60000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden){loadLibrary().catch(()=>{});refresh(true)}});
</script>
</body></html>"""

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
