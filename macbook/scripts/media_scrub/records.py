"""Mirrored wire records and their validation; the Pi side is pi/scripts/media_position.py.

The status JSON and the token schema are the contract with BTT; keep them in step
with the page code that consumes them."""
import base64
import binascii
import json
import math
import re

from . import config

SOURCES = ("vantv", "vlc", "none", "unknown")
SEEK_SOURCES = ("vantv", "vlc")
PAGE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_TEXT = 2048
MAX_MEDIA_ID = 512
MAX_TOKEN = 2048
MAX_SEQ = 2 ** 53


class RecordError(ValueError):
    pass


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _opt_text(value, limit):
    return value is None or (isinstance(value, str) and len(value) <= limit)


def validate_sample(line):
    """The sample record, or RecordError. Unknown extra keys are ignored."""
    if not isinstance(line, dict) or line.get("type") != "sample" or line.get("v") != 1:
        raise RecordError("not a v1 sample")
    if line.get("source") not in SOURCES:
        raise RecordError("bad source")
    for key in ("t", "position", "duration", "rate"):
        if not _number(line.get(key)):
            raise RecordError("bad " + key)
    if line["duration"] < 0 or line["rate"] < 0:
        raise RecordError("negative duration or rate")
    if not isinstance(line.get("state"), str) or len(line["state"]) > 64:
        raise RecordError("bad state")
    if not isinstance(line.get("can_seek"), bool):
        raise RecordError("bad can_seek")
    if not _opt_text(line.get("title"), MAX_TEXT) or not _opt_text(line.get("error"), MAX_TEXT) \
            or not _opt_text(line.get("media_id"), MAX_MEDIA_ID):
        raise RecordError("bad text field")
    return {key: line.get(key) for key in (
        "t", "source", "state", "title", "position", "duration", "rate",
        "can_seek", "media_id", "error")}


def validate_seek_result(line):
    if not isinstance(line, dict) or line.get("type") != "seek_result":
        raise RecordError("not a seek_result")
    req = line.get("req")
    if not isinstance(req, int) or isinstance(req, bool):
        raise RecordError("bad req")
    if not isinstance(line.get("ok"), bool) or not isinstance(line.get("message"), str):
        raise RecordError("bad result")
    return {"req": req, "ok": line["ok"], "message": line["message"][:MAX_TEXT]}


def validate_seek_request(data):
    """The page's seek request (token payload and ctl.sock line), or RecordError."""
    expected = {"page", "seq", "position", "source", "media_id"}
    if not isinstance(data, dict) or set(data) != expected:
        raise RecordError("unexpected fields")
    if not isinstance(data["page"], str) or not PAGE_PATTERN.match(data["page"]):
        raise RecordError("bad page")
    seq = data["seq"]
    if not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq <= MAX_SEQ:
        raise RecordError("bad seq")
    if not _number(data["position"]) or data["position"] < 0:
        raise RecordError("bad position")
    if data["source"] not in SEEK_SOURCES:
        raise RecordError("bad source")
    media_id = data["media_id"]
    if not isinstance(media_id, str) or not 0 < len(media_id) <= MAX_MEDIA_ID:
        raise RecordError("bad media_id")
    return {"page": data["page"], "seq": seq, "position": float(data["position"]),
            "source": data["source"], "media_id": media_id}


def parse_token(token):
    if not isinstance(token, str) or not 0 < len(token) <= MAX_TOKEN:
        raise RecordError("bad token length")
    try:
        raw = base64.b64decode(token, validate=True)
        data = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise RecordError("token is not base64 JSON")
    return validate_seek_request(data)


def _reject_constant(name):
    raise ValueError(name)


def is_seekable(sample, age):
    """The single seekability rule (contract); age is Mac-measured and must be 0..2."""
    return bool(sample and sample.get("can_seek") and sample.get("source") in SEEK_SOURCES
                and sample.get("media_id") and sample.get("duration", 0) > 0
                and age is not None and 0 <= age <= config.SEEKABLE_AGE_SECONDS)


def backoff_seconds(count):
    """Delay after the count-th consecutive watcher failure: 2, 4, 8, ... capped."""
    return min(config.BACKOFF_CAP_SECONDS, 2.0 ** max(1, count))


def build_status(snapshot, now):
    """The status JSON record from snapshot.json contents."""
    snapshot = snapshot or {}
    sample = snapshot.get("sample")
    received = snapshot.get("received_at")
    age = None
    if sample is not None and _number(received):
        age = now - received
    error = snapshot.get("error")
    if error is None and sample is not None:
        error = sample.get("error")
    # A negative age means the clock stepped back: fail closed like a stale sample.
    fresh = age is not None and 0 <= age <= config.FRESH_STATUS_SECONDS
    if error is None and not fresh:
        error = "no fresh sample" if age is None or age >= 0 else "clock stepped back"
    ok = fresh and error is None
    sample = sample or {}
    return {
        "ok": ok, "source": sample.get("source"), "state": sample.get("state"),
        "title": sample.get("title"), "position": sample.get("position"),
        "duration": sample.get("duration"), "rate": sample.get("rate"),
        "media_id": sample.get("media_id"),
        "seekable": bool(ok and is_seekable(sample, age)),
        "age": None if age is None else round(age, 3),
        "seek_ack": snapshot.get("seek_ack"), "error": error,
    }


def format_hms(seconds):
    total = int(max(0, seconds))
    return "%d:%02d:%02d" % (total // 3600, total % 3600 // 60, total % 60)


def status_text(status):
    """PPause's text: 'title \\nH:MM:SS / H:MM:SS', '' when nothing plays."""
    if not status["ok"]:
        return "Status unavailable"
    if status["source"] == "none":
        return ""
    title = re.sub(r"[\x00-\x1f\x7f]+", " ", status["title"] or "")
    return "%s \n%s / %s" % (title, format_hms(status["position"]), format_hms(status["duration"]))
