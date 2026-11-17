"""Shared fixtures for the media_scrub tests: a scripted fake Pi `serve`, a fake ssh that
execs it, and helpers that run the real entry script the way BTT does."""
import base64
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "macbook" / "scripts" / "media_scrub.py"
REAL_SERVE = ROOT / "pi" / "scripts" / "media_position.py"
SYSTEM_PYTHON = "/usr/bin/python3"

SAMPLE = {"source": "vlc", "state": "PLAYING", "title": "Movie", "position": 65.0,
          "duration": 5400.0, "rate": 1.0, "can_seek": True, "media_id": "vlc:item:7",
          "error": None}

# Scripted by the JSON file named in MEDIA_SCRUB_FAKE; speaks the Pi wire protocol.
FAKE_SERVE = r'''
import json, os, select, signal, sys, time
cfg = json.load(open(os.environ["MEDIA_SCRUB_FAKE"]))
def log(text):
    with open(cfg["log"], "a") as out:
        out.write(text + "\n")
log("start %d" % os.getpid())
sample = dict(cfg["sample"])
position = sample["position"]
interval = cfg.get("interval", 0.1)
emitted = 0
buffer = b""
eof = False
result_due = None
next_sample = time.monotonic()
def emit(record):
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()
while True:
    now = time.monotonic()
    timers = [next_sample] + ([result_due[0]] if result_due else [])
    wait = max(0.0, min(timers) - now)
    readable = [] if eof else select.select([0], [], [], wait)[0]
    if not readable and eof:
        time.sleep(wait)
    if readable:
        chunk = os.read(0, 65536)
        if not chunk:
            log("eof")
            if not cfg.get("ignore_eof"):
                sys.exit(0)
            eof = True
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            command = json.loads(line)
            log("seek " + json.dumps(command))
            result_due = (time.monotonic() + cfg.get("seek_delay", 0.0), command)
    now = time.monotonic()
    if result_due and now >= result_due[0]:
        command = result_due[1]
        result_due = None
        if cfg.get("stray_req"):
            emit({"type": "seek_result", "req": cfg["stray_req"], "ok": True, "message": "stray"})
        ok = cfg.get("result_ok", True)
        if ok:
            position = command["position"]
        emit({"type": "seek_result", "req": command["req"], "ok": ok,
              "message": "sought" if ok else "refused"})
        next_sample = now + cfg.get("hold_after_seek", 0.0)
    if now >= next_sample and not cfg.get("no_samples"):
        limit = cfg.get("stop_samples_after")
        if limit is None or emitted < limit:
            record = dict(sample, type="sample", v=1, t=time.time(), position=position)
            emit(record)
            emitted += 1
        next_sample = now + interval
'''


class ScrubCase(unittest.TestCase):
    idle_seconds = "6"

    def setUp(self):
        # Unix socket paths top out near 104 bytes on macOS, so keep this short.
        self.dir = tempfile.mkdtemp(prefix="ms", dir="/tmp")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.state = os.path.join(self.dir, "s")
        os.mkdir(self.state, 0o700)
        self.log = os.path.join(self.dir, "fake.log")
        self.serve_script = os.path.join(self.dir, "serve.py")
        Path(self.serve_script).write_text(FAKE_SERVE)
        self.config_path = os.path.join(self.dir, "fake.json")
        self.set_ssh(self.write_ssh("fake-ssh", 'exec "%s" -I "%s"' % (sys.executable, self.serve_script)))
        self.configure()
        self.addCleanup(self.kill_everything)

    # --- fake ssh and fake Pi ----------------------------------------
    def write_ssh(self, name, body):
        path = os.path.join(self.dir, name)
        Path(path).write_text("#!/bin/sh\n" + body + "\n")
        os.chmod(path, 0o755)
        return path

    def set_ssh(self, path):
        self.ssh = path

    def configure(self, **overrides):
        data = {"log": self.log, "sample": dict(SAMPLE), "interval": 0.1}
        data.update(overrides)
        Path(self.config_path).write_text(json.dumps(data))

    def env(self, **extra):
        env = dict(os.environ)
        env.update(MEDIA_SCRUB_STATE_DIR=self.state, MEDIA_SCRUB_SSH=self.ssh,
                   MEDIA_SCRUB_FAKE=self.config_path, MEDIA_SCRUB_IDLE_SECONDS=self.idle_seconds)
        env.update(extra)
        return env

    # --- running the helper ------------------------------------------
    def argv(self, *args):
        return [SYSTEM_PYTHON, "-B", str(ENTRY)] + list(args)

    def run_cli(self, *args, timeout=20, **extra_env):
        return subprocess.run(self.argv(*args), env=self.env(**extra_env), capture_output=True,
                              text=True, timeout=timeout)

    def popen_cli(self, *args):
        return subprocess.Popen(self.argv(*args), env=self.env(), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

    def status(self, wait=5):
        result = self.run_cli("status", "--wait", str(wait))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def start_watcher(self):
        status = self.status()
        self.assertTrue(status["ok"], status)
        return status

    def seek(self, position=100.0, seq=1, page="p1", source="vlc", media_id="vlc:item:7"):
        result = self.run_cli("seek", "--b64", token(page=page, seq=seq, position=position,
                                                      source=source, media_id=media_id))
        return result.returncode, json.loads(result.stdout)

    # --- observation helpers -----------------------------------------
    def watcher_pid(self):
        info = self.read_state("watch.json")
        return info["pid"] if info else None

    def read_state(self, name):
        try:
            with open(os.path.join(self.state, name)) as source:
                return json.load(source)
        except (OSError, ValueError):
            return None

    def log_lines(self, prefix):
        try:
            lines = Path(self.log).read_text().splitlines()
        except OSError:
            return []
        return [line for line in lines if line.startswith(prefix)]

    def serve_pids(self):
        return [int(line.split()[1]) for line in self.log_lines("start")]

    def seeks(self):
        return [json.loads(line[5:]) for line in self.log_lines("seek ")]

    def wait_for(self, condition, timeout=10.0, message="condition"):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.05)
        self.fail("timed out waiting for " + message)

    def wait_gone(self, pid, timeout=10.0):
        self.wait_for(lambda: not alive(pid), timeout, "pid %s to exit" % pid)

    def kill_everything(self):
        pids = [self.watcher_pid()] + self.serve_pids()
        for pid in pids:
            if pid and alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie child still answers kill(0); the watcher's children are reaped by the
    # watcher itself and the watcher is reparented, so this is a real liveness check.
    result = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")


def token(**fields):
    base = {"page": "p1", "seq": 1, "position": 10.0, "source": "vlc", "media_id": "vlc:item:7"}
    base.update(fields)
    return base64.b64encode(json.dumps(base).encode()).decode()
