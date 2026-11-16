"""Shared harness for the deferred.zsh tests: a temporary job root, a loopback
stand-in for the ntfy topics and a CLI runner. launchd is never touched."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import unittest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "macbook" / "scripts" / "deferred.zsh"


class WarningTopic(BaseHTTPRequestHandler):
    received: list = []
    paths: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        self.paths.append(self.path)
        self.received.append((self.headers.get("Title"), self.headers.get("Priority"), body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class DeferredHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="deferred-tests-")).resolve()
        self.addCleanup(subprocess.run, ["/bin/rm", "-rf", str(self.tmp)])
        self.root = self.tmp / "root"
        WarningTopic.received, WarningTopic.paths = [], []
        server = ThreadingHTTPServer(("127.0.0.1", 0), WarningTopic)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        secrets = self.tmp / "secrets"
        base = f"http://127.0.0.1:{server.server_port}"
        secrets.write_text(f"export NTFY_WARNING_URL='{base}/warn'\nexport NTFY_MACBOOK_URL='{base}/agent'\n")
        self.env = {
            "HOME": os.environ["HOME"],
            "PATH": "/usr/bin:/bin",
            "DEFERRED_ROOT": str(self.root),
            "DEFERRED_SECRETS": str(secrets),
            "DEFERRED_LABEL": "com.jacobr.deferred-jobs-test",
            "DEFERRED_WAKE_GRACE": "0",
            "DEFERRED_LOCK_WAIT": "0.1",
        }

    def deferred(self, *args, check=True):
        result = subprocess.run(
            ["/bin/zsh", str(SCRIPT), *args], env=self.env, cwd=self.tmp,
            capture_output=True, text=True, timeout=60,
        )
        if check and result.returncode != 0:
            self.fail(f"deferred {' '.join(args)} exited {result.returncode}: {result.stderr}")
        return result

    def script(self, body, name="job.zsh"):
        path = self.tmp / name
        path.write_text("#!/bin/zsh\n" + body + "\n")
        return path

    def add(self, due, name, body, *options):
        return Path(self.deferred("add", *options, due, name, str(self.script(body))).stdout.strip())

    def meta(self, job):
        return dict(line.split("=", 1) for line in (job / "job").read_text().splitlines())

    def listed(self, job):
        """The `list --json` record of one job directory."""
        jobs = json.loads(self.deferred("list", "--json").stdout)["jobs"]
        return next(record for record in jobs if record["id"] == job.name)

    def hold_checker_lock(self):
        lock = self.root / "lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen(
            [
                "/bin/zsh", "-c",
                'zmodload zsh/system; : >> "$1"; zsystem flock "$1"; '
                'print -r -- ready; sleep 30',
                "lock-holder", str(lock),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

        def stop_holder():
            os.killpg(holder.pid, signal.SIGTERM)
            holder.communicate(timeout=5)

        self.addCleanup(stop_holder)
        self.assertEqual(holder.stdout.readline().strip(), "ready")
        return holder
