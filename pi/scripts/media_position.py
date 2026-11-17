#!/usr/bin/env python3
"""Playback position and seeking for the Pi's media players, over one JSON-lines stream.

Runs on the Pi (deployed flat to /home/pi/scripts/, started as
`/usr/bin/python3 -I media_position.py ...`); stdlib only, 3.9-compatible so the
Mac tests can run it too. Reads the localhost APIs of Van TV (port 8795) and the
video library (port 8789, VLC) and never talks to anything else.

Wire contract (canonical statement; the Mac helper macbook/scripts/media_scrub.py
mirrors it by hand because the flat Pi script and the Mac checkout share no import
root). UTF-8 JSON, one object per line.

Pi to Mac, sample (every --interval seconds, and right after each seek result):
  {"type":"sample","v":1,"t":<pi unix float>,
   "source":"vantv"|"vlc"|"none"|"unknown","state":str,"title":str|null,
   "position":float,"duration":float,"rate":float,"can_seek":bool,
   "media_id":str|null,"error":str|null}
  media_id: Van TV "vantv:<status.instance>:<video.id>"; VLC "vlc:item:<item.id>"
  only when the video library reports a catalog item, else null.
  source "unknown": Van TV's status could not be classified (timeout, non-200, bad
  JSON); VLC is not consulted that tick. "none": nothing is playing.
  Van TV counts as inactive only on a valid 200 JSON whose player is not
  LOADING/PLAYING/PAUSED, or on connection refused; then VLC is consulted.
  rate: VLC player.rate, Van TV speed, default 1.0.

Pi to Mac, seek result: {"type":"seek_result","req":int,"ok":bool,"message":str}
  followed at once by a fresh sample.

Mac to Pi, seek: {"type":"seek","req":int,"position":float,
                  "source":"vantv"|"vlc","media_id":str}
  The Pi re-reads status right before acting and refuses on a source or media_id
  mismatch, an unseekable player, or a position that is negative, non-finite or
  beyond the duration. A line that is not a valid seek gets a seek_result with
  ok:false when it carries an int req, and is otherwise ignored (logged to stderr).
  Van TV: POST /api/control {"action":"seek","position":p}, success only on 200
  with {"action":"seek","done":true}. VLC: POST /api/position form position=p,
  success only on 200 with {"ok":true}.

Commands: `status` prints one sample; `serve [--interval 0.5] [--max-seconds 1800]`
streams samples and executes seeks read from stdin, serialized with sampling on one
thread; it exits on stdin EOF, a closed stdout, SIGTERM/SIGHUP or max-seconds;
`seek POSITION --source S --media-id ID` is the one-shot form (prints
{"ok","message"}, exit 0/1).

Base URLs (including /api) come from MEDIA_POSITION_VANTV_URL and
MEDIA_POSITION_VIDEO_URL, defaulting to the localhost services.
"""

import argparse
import json
import math
import os
import queue
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL_VERSION = 1
DEFAULT_VANTV_URL = "http://localhost:8795/api"
DEFAULT_VIDEO_URL = "http://localhost:8789/api"
REQUEST_TIMEOUT = 1.0
VANTV_ACTIVE_STATES = ("LOADING", "PLAYING", "PAUSED")
SEEKABLE_STATES = ("PLAYING", "PAUSED")
SOURCES = ("vantv", "vlc")
STOP_POLL_SECONDS = 0.1


class Unreachable(Exception):
    """The service refused the connection (it is not running)."""


class BadResponse(Exception):
    """Timeout, non-200 status or an unparseable body: the state is unknown."""


def base_url(variable, default):
    return os.environ.get(variable, default).rstrip("/")


def request_json(method, url, data=None, content_type=None):
    """Return (status, parsed JSON). Raise Unreachable on refusal, BadResponse otherwise."""
    request = urllib.request.Request(url, data=data, method=method)
    if content_type:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            status, body = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, body = error.code, error.read()
    except urllib.error.URLError as error:
        if isinstance(error.reason, ConnectionRefusedError):
            raise Unreachable(str(error.reason))
        raise BadResponse(str(error.reason))
    except (OSError, socket.timeout, ValueError) as error:
        raise BadResponse(str(error) or type(error).__name__)
    try:
        return status, json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise BadResponse("response was not JSON (status %d)" % status)


def number(value, default=0.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value) if math.isfinite(value) else default


def text_or_none(value):
    return value if isinstance(value, str) and value else None


def blank_sample(source, state, error=None):
    return {
        "type": "sample", "v": PROTOCOL_VERSION, "t": time.time(),
        "source": source, "state": state, "title": None, "position": 0.0,
        "duration": 0.0, "rate": 1.0, "can_seek": False, "media_id": None,
        "error": error,
    }


def vantv_sample(status):
    """Sample from an active Van TV status body."""
    state = status["player"]
    video = status.get("video") if isinstance(status.get("video"), dict) else None
    sample = blank_sample("vantv", state)
    duration = number(status.get("duration"))
    sample.update(
        title=text_or_none(video.get("title")) if video else None,
        position=max(0.0, number(status.get("position"))),
        duration=max(0.0, duration),
        rate=number(status.get("speed"), 1.0) or 1.0,
    )
    video_id, instance = (video or {}).get("id"), status.get("instance")
    if isinstance(video_id, str) and video_id and isinstance(instance, str) and instance:
        sample["media_id"] = "vantv:%s:%s" % (instance, video_id)
    sample["can_seek"] = video is not None and state in SEEKABLE_STATES and duration > 0
    return sample


def vlc_sample(status):
    player = status.get("player") if isinstance(status, dict) else None
    if not isinstance(player, dict) or not player.get("available"):
        return blank_sample("none", "OFFLINE")
    state = str(player.get("state") or "STOPPED").upper()
    if state not in SEEKABLE_STATES:
        return blank_sample("none", state)
    sample = blank_sample("vlc", state)
    item = player.get("item") if isinstance(player.get("item"), dict) else None
    item_id = item.get("id") if item else None
    duration = number(player.get("duration"))
    sample.update(
        title=text_or_none(player.get("title")),
        position=max(0.0, number(player.get("position"))),
        duration=max(0.0, duration),
        rate=number(player.get("rate"), 1.0) or 1.0,
        can_seek=bool(player.get("can_seek")) and duration > 0,
    )
    if isinstance(item_id, (str, int)) and not isinstance(item_id, bool) and str(item_id):
        sample["media_id"] = "vlc:item:%s" % item_id
    return sample


def take_sample():
    """One sample, at most two GETs: Van TV first, the video library only if Van TV is inactive."""
    try:
        status, body = request_json("GET", base_url("MEDIA_POSITION_VANTV_URL", DEFAULT_VANTV_URL) + "/status")
    except Unreachable:
        body = None
    except BadResponse as error:
        return blank_sample("unknown", "UNKNOWN", "Van TV status: %s" % error)
    else:
        if status != 200 or not isinstance(body, dict):
            return blank_sample("unknown", "UNKNOWN", "Van TV status unusable (HTTP %d)" % status)
        if body.get("player") in VANTV_ACTIVE_STATES:
            return vantv_sample(body)
    try:
        status, body = request_json("GET", base_url("MEDIA_POSITION_VIDEO_URL", DEFAULT_VIDEO_URL) + "/status")
    except (Unreachable, BadResponse) as error:
        return blank_sample("none", "OFFLINE", "video library status: %s" % error)
    if status != 200:
        return blank_sample("none", "OFFLINE", "video library status HTTP %d" % status)
    return vlc_sample(body)


def failure_message(body, status):
    if isinstance(body, dict):
        for key in ("message", "error"):
            if isinstance(body.get(key), str) and body[key]:
                return body[key]
    return "HTTP %d" % status


def post_seek(source, position):
    """Send the seek to its player; success only when the body says so."""
    try:
        if source == "vantv":
            payload = json.dumps({"action": "seek", "position": position}).encode()
            status, body = request_json(
                "POST", base_url("MEDIA_POSITION_VANTV_URL", DEFAULT_VANTV_URL) + "/control",
                payload, "application/json")
            done = isinstance(body, dict) and body.get("action") == "seek" and body.get("done") is True
        else:
            payload = urllib.parse.urlencode({"position": repr(position)}).encode()
            status, body = request_json(
                "POST", base_url("MEDIA_POSITION_VIDEO_URL", DEFAULT_VIDEO_URL) + "/position",
                payload, "application/x-www-form-urlencoded")
            done = isinstance(body, dict) and body.get("ok") is True
    except (Unreachable, BadResponse) as error:
        return False, "seek failed: %s" % error
    if status == 200 and done:
        return True, "sought to %.1f" % position
    return False, "seek refused: %s" % failure_message(body, status)


def seek(position, source, media_id):
    """Validate against a fresh sample, then dispatch. Returns (ok, message)."""
    if isinstance(position, bool) or not isinstance(position, (int, float)) or not math.isfinite(position):
        return False, "position must be a finite number"
    if position < 0:
        return False, "position must not be negative"
    if source not in SOURCES:
        return False, "source must be vantv or vlc"
    if not isinstance(media_id, str) or not media_id:
        return False, "media_id is required"
    sample = take_sample()
    if sample["source"] != source:
        return False, "playback source is %s, not %s" % (sample["source"], source)
    if sample["media_id"] != media_id:
        return False, "the media changed"
    if not sample["can_seek"]:
        return False, "the player is not seekable"
    if position > sample["duration"]:
        return False, "position is beyond the duration (%.1f)" % sample["duration"]
    return post_seek(source, float(position))


class Emitter:
    """Writes flushed JSON lines; `closed` once stdout broke."""

    def __init__(self, stream):
        self.stream = stream
        self.closed = False

    def send(self, record):
        if self.closed:
            return
        try:
            self.stream.write(json.dumps(record, separators=(",", ":")) + "\n")
            self.stream.flush()
        except (BrokenPipeError, OSError, ValueError):
            self.closed = True


def read_commands(stream, commands):
    """Reader thread: queue parsed stdin lines; None marks EOF."""
    try:
        for line in stream:
            commands.put(line)
    except (OSError, ValueError):
        pass
    finally:
        commands.put(None)


def int_req(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def handle_command(line, emitter):
    """Execute one stdin line; returns True when a seek result was emitted."""
    try:
        command = json.loads(line)
    except ValueError:
        command = None
    if not isinstance(command, dict) or command.get("type") != "seek":
        req = int_req(command.get("req")) if isinstance(command, dict) else None
        print("media_position: ignoring malformed line", file=sys.stderr, flush=True)
        if req is None:
            return False
        emitter.send({"type": "seek_result", "req": req, "ok": False, "message": "malformed request"})
        return True
    req = int_req(command.get("req"))
    if req is None:
        print("media_position: seek without an integer req", file=sys.stderr, flush=True)
        return False
    ok, message = seek(command.get("position"), command.get("source"), command.get("media_id"))
    emitter.send({"type": "seek_result", "req": req, "ok": ok, "message": message})
    return True


def serve(interval, max_seconds, stdin=None, stdout=None):
    emitter = Emitter(stdout or sys.stdout)
    commands = queue.Queue()
    stop = threading.Event()
    for name in ("SIGTERM", "SIGHUP"):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    threading.Thread(target=read_commands, args=(stdin or sys.stdin, commands), daemon=True).start()
    deadline = time.monotonic() + max_seconds
    next_tick = time.monotonic()
    while not stop.is_set() and not emitter.closed and time.monotonic() < deadline:
        if time.monotonic() >= next_tick:
            emitter.send(take_sample())
            next_tick = time.monotonic() + interval
            continue
        wait = min(STOP_POLL_SECONDS, next_tick - time.monotonic(), max(0.0, deadline - time.monotonic()))
        try:
            line = commands.get(timeout=max(0.0, wait))
        except queue.Empty:
            continue
        if line is None:
            break  # stdin EOF: the Mac side is gone
        if handle_command(line, emitter):
            emitter.send(take_sample())
            next_tick = time.monotonic() + interval
    return 0


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="media_position.py", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    serving = commands.add_parser("serve")
    serving.add_argument("--interval", type=float, default=0.5)
    serving.add_argument("--max-seconds", type=float, default=1800.0)
    seeking = commands.add_parser("seek")
    seeking.add_argument("position", type=float)
    seeking.add_argument("--source", required=True, choices=SOURCES)
    seeking.add_argument("--media-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "serve" and not (args.interval > 0 and args.max_seconds > 0):
        parser.error("--interval and --max-seconds must be positive")
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "status":
        print(json.dumps(take_sample(), separators=(",", ":")))
        return 0
    if args.command == "seek":
        ok, message = seek(args.position, args.source, args.media_id)
        print(json.dumps({"ok": ok, "message": message}))
        return 0 if ok else 1
    try:
        return serve(args.interval, args.max_seconds)
    finally:
        try:
            sys.stdout.flush()
        except (BrokenPipeError, OSError):
            # Point stdout at /dev/null so interpreter shutdown doesn't print a flush error.
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


if __name__ == "__main__":
    sys.exit(main())
