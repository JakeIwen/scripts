"""The `seek --b64` command: one request to the watcher's ctl.sock, one verdict back."""
import json
import os
import socket
import stat
import time

from . import config
from .records import RecordError, parse_token


def _print(result):
    print(json.dumps(result))


def run_seek(state, token):
    try:
        request = parse_token(token)
    except RecordError as error:
        _print({"ok": False, "superseded": False, "reason": "invalid", "message": str(error)})
        return 2
    state.touch_heartbeat()
    result = _send(state, request)
    _print(result)
    return 0 if result.get("ok") else 1


def _fail(message, reason="unavailable"):
    return {"ok": False, "superseded": False, "reason": reason, "message": message}


def _send(state, request):
    try:
        state.check_socket_path()
        info = os.lstat(state.socket)
    except OSError:
        return _fail("no watcher is running")
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        return _fail("ctl.sock is not a private socket")
    deadline = time.monotonic() + config.SEEK_WAIT_SECONDS
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(config.SEEK_WAIT_SECONDS)
        sock.connect(state.socket)
        sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data and len(data) < 8192:
            sock.settimeout(max(0.05, deadline - time.monotonic()))
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
    except (OSError, ValueError):
        return _fail("watcher did not answer")
    finally:
        sock.close()
