"""The `status` command: heartbeat, guarded watcher spawn, optional cold-start wait."""
import json
import os
import subprocess
import sys
import time

from . import config
from .records import build_status, status_text


def maybe_spawn(state, script):
    """Start the watcher unless one runs, one was just claimed or the failure budget says wait."""
    if state.watcher_alive():
        return False
    with state.try_lock(state.spawn_lock) as claimed:
        if not claimed or state.watcher_alive():
            return False
        now = time.time()
        claim = state.read_json(state.spawn_info) or {}
        if 0 <= now - claim.get("at", -1e9) < config.SPAWN_CLAIM_SECONDS:
            return False
        if (state.read_json(state.failures) or {}).get("next_start_at", 0) > now:
            return False
        state.write_json(state.spawn_info, {"at": now})
        log = os.open(state.log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            subprocess.Popen([sys.executable, "-B", script, "watch"], stdin=subprocess.DEVNULL,
                             stdout=log, stderr=log, start_new_session=True, close_fds=True)
        finally:
            os.close(log)
        return True


def _gave_up(state):
    failures = state.read_json(state.failures) or {}
    return failures.get("next_start_at", 0) > time.time() and not state.watcher_alive()


def current_status(state):
    return build_status(state.read_json(state.snapshot), time.time())


def run_status(state, script, text, wait):
    state.touch_heartbeat()
    deadline = time.monotonic() + max(0.0, wait)
    last_touch = time.monotonic()
    while True:
        maybe_spawn(state, script)
        status = current_status(state)
        if status["ok"] or time.monotonic() >= deadline or _gave_up(state):
            break
        time.sleep(0.05)
        if time.monotonic() - last_touch > 0.5:
            state.touch_heartbeat()
            last_touch = time.monotonic()
    print(status_text(status) if text else json.dumps(status))
    return 0
