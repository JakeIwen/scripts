"""The shared ntfy warning sender delivers redacted text and never reveals its URL."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from macbook.scripts.ntfy_warning import send_warning

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ntfy_warning.py"


class WarningTopic(BaseHTTPRequestHandler):
    received: list = []
    status = 200

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        self.received.append({"path": self.path, "body": body,
                              **{key: self.headers.get(key) for key in ("Title", "Priority", "Tags")}})
        self.send_response(self.status)
        self.end_headers()

    def log_message(self, *args):
        pass


class NtfyWarningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ntfy-warning-tests-"))
        self.addCleanup(subprocess.run, ["/bin/rm", "-rf", str(self.tmp)])
        WarningTopic.received, WarningTopic.status = [], 200
        server = ThreadingHTTPServer(("127.0.0.1", 0), WarningTopic)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}/private-topic"
        self.secrets = self.secrets_file(self.url)

    def secrets_file(self, url):
        path = self.tmp / "secrets"
        path.write_text(f"export NTFY_WARNING_URL='{url}'\n")
        return path

    def send(self, message, *args):
        return subprocess.run(
            ["/usr/bin/python3", "-I", "-B", str(SCRIPT), "--secrets", str(self.secrets), *args],
            input=message, capture_output=True, text=True, timeout=60,
        )

    def test_cli_posts_stdin_to_the_warning_topic(self):
        result = self.send("job kept\n", "--title", "Deferred job failed: x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(WarningTopic.received, [{
            "path": "/private-topic", "body": "job kept\n",
            "Title": "Deferred job failed: x", "Priority": "high", "Tags": "warning",
        }])

    def test_import_sends_with_the_given_priority(self):
        self.assertIsNone(send_warning("healthy", "all good", "default", secrets=self.secrets))
        self.assertEqual([(m["Title"], m["Priority"]) for m in WarningTopic.received],
                         [("healthy", "default")])

    def test_unusable_url_is_refused_without_revealing_it(self):
        for url in ("ftp://127.0.0.1/topic", "http://user:pass@127.0.0.1/topic", ""):
            with self.subTest(url=url):
                self.secrets = self.secrets_file(url)
                result = self.send("x", "--title", "t")
                self.assertEqual(result.returncode, 1)
                self.assertIn("no usable NTFY_WARNING_URL", result.stderr)
                if url:
                    self.assertNotIn(url, result.stderr)
        self.secrets = self.tmp / "absent"
        self.assertEqual(self.send("x", "--title", "t").returncode, 1)
        self.assertEqual(WarningTopic.received, [])

    def test_rejected_delivery_fails_without_revealing_the_url(self):
        WarningTopic.status = 500
        result = self.send("x", "--title", "t")
        self.assertEqual(result.returncode, 1)
        self.assertIn("delivery failed", result.stderr)
        self.assertNotIn("private-topic", result.stderr)


if __name__ == "__main__":
    unittest.main()
