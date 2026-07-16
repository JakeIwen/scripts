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
        last_scan = time.time()
        return
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

state_lock = threading.Lock()

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
    for candidate in (name, state.get("device")):
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
    entry = state["books"].get(book.rel)
    if not entry:
        return None
    return {"track": entry["track"], "tracks_total": entry.get("tracks_total", len(book.tracks)),
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

    entry = state["books"].get(book.rel)
    from_track, from_secs = 0, 0
    if entry and not restart and not entry.get("finished"):
        from_track = min(entry.get("track", 0), len(book.tracks) - 1)
        from_secs = max(0, entry.get("pos", 0) - RESUME_REWIND)
    if restart:
        state["books"].pop(book.rel, None)

    try:
        start_book(book, device, from_track, from_secs)
    except Exception as e:
        return jsonify({"ok": False, "message": f"playback failed: {e}"}), 502

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
    devname = state.get("device")
    zmap = get_zones()
    if devname and devname in zmap:
        return zmap[devname].group.coordinator
    return get_device()

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
            member.volume = int(setval)
        elif delta:
            member.volume = max(0, min(100, member.volume + delta))
    return jsonify({"ok": True, "volume": dev.volume})

@app.route("/api/status")
def api_status():
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
    scan_library()
    with books_lock:
        n = len(books)
    return jsonify({"ok": True, "count": n})

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
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#12100e">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📖</text></svg>">
<title>Audiobooks</title>
<style>
:root{--bg:#12100e;--card:#1e1b18;--ink:#e8e2d9;--dim:#9a9084;--accent:#e8a33d;--line:#2e2a25}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--ink);font:16px/1.45 -apple-system,system-ui,sans-serif;
  padding:12px 12px calc(150px + env(safe-area-inset-bottom));max-width:640px;margin:0 auto}
h1{font-size:20px;margin:6px 2px 12px;letter-spacing:.02em}
#q{width:100%;padding:12px 14px;font-size:17px;border-radius:12px;border:1px solid var(--line);
  background:var(--card);color:var(--ink);outline:none;margin-bottom:14px}
h2{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.1em;margin:16px 2px 8px}
.book{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:11px 13px;
  margin-bottom:8px;display:flex;align-items:center;gap:10px;cursor:pointer}
.book .t{flex:1;min-width:0}
.book .n{font-size:15px;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;
  -webkit-line-clamp:2;-webkit-box-orient:vertical}
.book .p{font-size:12px;color:var(--accent);margin-top:2px}
.book .sub{color:var(--dim);font-size:12px}
.restart{color:var(--dim);font-size:18px;padding:6px;background:none;border:none}
#bar{position:fixed;left:0;right:0;bottom:0;background:#191612ee;backdrop-filter:blur(12px);
  border-top:1px solid var(--line);padding:10px 14px calc(10px + env(safe-area-inset-bottom));
  max-width:640px;margin:0 auto}
#bar .n{font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#bar .meta{font-size:12px;color:var(--dim);margin:2px 0 8px}
#prog{height:3px;background:var(--line);border-radius:2px;margin-bottom:10px}
#prog>div{height:100%;background:var(--accent);border-radius:2px;width:0}
.row{display:flex;justify-content:space-between;align-items:center;gap:6px}
.row button{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:10px;
  font-size:15px;padding:10px 0;flex:1}
.row button:active{background:var(--line)}
#pp{font-size:20px;flex:1.3;color:var(--accent)}
.hide{display:none}
</style></head><body>
<h1>📖 Audiobooks</h1>
<input id="q" placeholder="Search…" autocomplete="off">
<div id="cont"></div>
<div id="lib"></div>
<div id="bar" class="hide">
  <div class="n" id="np-name"></div>
  <div class="meta" id="np-meta"></div>
  <div id="prog"><div id="progfill"></div></div>
  <div class="row">
    <button onclick="api('chapter?d=-1')">⏮</button>
    <button onclick="api('skip?s=-60')">-60</button>
    <button id="pp" onclick="api('toggle')">⏯</button>
    <button onclick="api('skip?s=60')">+60</button>
    <button onclick="api('chapter?d=1')">⏭</button>
    <button onclick="api('volume?d=-6')">🔉</button>
    <button onclick="api('volume?d=6')">🔊</button>
  </div>
</div>
<script>
let all=[];
const $=id=>document.getElementById(id);
async function j(u){const r=await fetch(u);return r.json()}
async function api(ep){await j('/api/'+ep);setTimeout(refresh,600)}
function fmt(b){
  const pr=b.progress;
  let sub=b.tracks+' chapters';
  let p='';
  if(pr&&!pr.finished)p='Ch '+(pr.track+1)+'/'+pr.tracks_total+' · '+pr.pos_hms;
  if(pr&&pr.finished)p='✓ finished';
  return `<div class="book" onclick="play('${b.id}')">
    <div class="t"><div class="n">${esc(b.name)}</div>
    ${p?`<div class="p">${p}</div>`:''}<div class="sub">${sub}</div></div>
    ${pr?`<button class="restart" onclick="event.stopPropagation();restart('${b.id}')">↺</button>`:''}
  </div>`}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;')}
function render(){
  const f=$('q').value.toLowerCase().split(/\\s+/).filter(Boolean);
  const vis=all.filter(b=>f.every(t=>b.name.toLowerCase().includes(t)));
  const cont=vis.filter(b=>b.progress&&!b.progress.finished&&b.progress.updated);
  cont.sort((a,c)=>c.progress.updated-a.progress.updated);
  $('cont').innerHTML=cont.length?'<h2>Continue</h2>'+cont.slice(0,5).map(fmt).join(''):'';
  const ids=new Set(cont.slice(0,5).map(b=>b.id));
  $('lib').innerHTML='<h2>Library</h2>'+vis.filter(b=>!ids.has(b.id)).map(fmt).join('');
}
async function play(id){await j('/api/play?book='+id);setTimeout(refresh,800)}
async function restart(id){
  if(confirm('Start over from the beginning?')){await j('/api/play?book='+id+'&restart=1');setTimeout(refresh,800)}}
async function load(){all=(await j('/api/books')).books;render()}
async function refresh(){
  try{
    const s=await j('/api/status');
    if(s.playing){
      $('bar').classList.remove('hide');
      $('np-name').textContent=s.playing.name;
      $('np-meta').textContent='Ch '+(s.playing.track+1)+'/'+s.playing.tracks_total
        +' · '+s.playing.position+' / '+s.playing.duration
        +(s.transport==='PLAYING'?'':' · paused')+' · '+s.device+' · vol '+s.volume;
      $('pp').textContent=s.transport==='PLAYING'?'⏸':'▶';
      const p=hms(s.playing.position),d=hms(s.playing.duration);
      $('progfill').style.width=(d?100*p/d:0)+'%';
    } else $('bar').classList.add('hide');
  }catch(e){}
}
function hms(x){if(!x)return 0;return x.split(':').reduce((a,v)=>a*60+ +v,0)}
$('q').addEventListener('input',render);
load();refresh();
setInterval(()=>{if(!document.hidden)refresh()},5000);
setInterval(()=>{if(!document.hidden)load()},60000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden){load();refresh()}});
</script>
</body></html>"""

@app.route("/")
def index():
    return PAGE

# ------------------------------------------------------------------- main ---

if __name__ == "__main__":
    scan_library()
    threading.Thread(target=poller_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
