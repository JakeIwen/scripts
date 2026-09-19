"""Exercise the actual shell monitor with bounded command doubles."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MonitorTests(unittest.TestCase):
    def run_monitor(self, google="204", cloudflare="204", rc="0", up="true", missing=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mock = root / "mock"
            mock.write_text('''#!/bin/sh
case "${0##*/}" in
ubus) printf '{"up":true}\\n' ;;
jsonfilter) cat >/dev/null; echo "$MOCK_UP" ;;
mwan3)
  [ "$1" = use ] && [ "$2" = wan ] || exit 99
  shift 2
  exec "$@"
  ;;
curl)
  printf '%s\\n' "$*" >> "$MOCK_CALLS"
  case "$*" in
    *www.google.com*) echo "$MOCK_GOOGLE" ;;
    *cp.cloudflare.com*) echo "$MOCK_CLOUDFLARE" ;;
    *) exit 99 ;;
  esac
  exit "$MOCK_RC"
  ;;
logger) echo "$*" >> "$MOCK_LOG" ;;
esac
''')
            mock.chmod(0o755)
            env = dict(os.environ, INTERFACE="wan", STATE_DIR=str(root / "state"),
                       MAX_SAMPLES="1", DATE="/bin/date", MOCK_UP=up,
                       MOCK_GOOGLE=google, MOCK_CLOUDFLARE=cloudflare, MOCK_RC=rc,
                       MOCK_CALLS=str(root / "calls"), MOCK_LOG=str(root / "log"),
                       MWAN_WRAP=str(mock))
            for name in ("ubus", "jsonfilter", "mwan3", "curl", "logger"):
                path = root / name
                path.symlink_to(mock)
                env[name.upper()] = str(path)
            if missing:
                env["MWAN_WRAP"] = str(root / "missing-library")
            subprocess.run(["/bin/sh", str(ROOT / "usr/libexec/uplink-https-monitor")],
                           env=env, check=True, timeout=5)
            fields = (root / "state/wan").read_text().strip().split("|")
            calls = (root / "calls").read_text() if (root / "calls").exists() else ""
            self.assertIn("interface=wan", (root / "log").read_text())
            return fields, calls

    def test_success_requires_both_expected_responses(self):
        fields, calls = self.run_monitor()
        self.assertEqual(fields[2:], ["online", "204", "0", "204", "0"])
        self.assertEqual(len(calls.splitlines()), 2)
        self.assertIn("--max-time 8", calls)
        self.assertIn("--noproxy *", calls)
        self.assertNotIn("--insecure", calls)
        self.assertNotIn("--location", calls)

    def test_redirect_and_server_failure_are_not_success(self):
        self.assertEqual(self.run_monitor(google="302")[0][2], "degraded")
        self.assertEqual(self.run_monitor(google="200", cloudflare="503")[0][2], "offline")

    def test_timeout_and_partial_transfer_are_not_success(self):
        self.assertEqual(self.run_monitor(google="000", cloudflare="000", rc="28")[0][2], "offline")
        self.assertEqual(self.run_monitor(rc="18")[0][2], "offline")

    def test_down_unknown_and_missing_dependency_do_not_probe(self):
        for args, expected in (({"up": "false"}, "down"),
                               ({"up": ""}, "unknown"), ({"missing": True}, "unknown")):
            fields, calls = self.run_monitor(**args)
            self.assertEqual(fields[2], expected)
            self.assertEqual(calls, "")


if __name__ == "__main__":
    unittest.main()
