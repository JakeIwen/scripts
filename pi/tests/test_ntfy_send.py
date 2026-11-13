"""ntfy_send.sh posts its arguments literally, against a local listener (no real alert).

Isolation: every run points NTFY_SECRETS_FILE at a scratch settings file that
routes to the listener, so the Pi's real settings (and its topic) are never
read, and setUp first proves the script reads that scratch file instead,
with a probe run that cannot post either way.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shlex
import subprocess
import tempfile
import threading
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'ntfy_send.sh'
PROBE = 'SCRATCH-SETTINGS-READ'


class Recorder(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.server.posts.append((self.headers, body.decode()))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@unittest.skipUnless(Path('/usr/bin/curl').exists(), 'ntfy_send.sh calls /usr/bin/curl by path')
class NtfySendTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Recorder)
        self.server.posts = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.settings = self.scratch / 'settings'
        self.route_to(f'http://127.0.0.1:{self.server.server_address[1]}/topic')
        probe = self.scratch / 'probe'
        probe.write_text(f'echo {PROBE}; exit 0\n')
        # No message argument: a script that ignored the probe would stop at
        # its usage check, before curl, instead of posting with real settings.
        result = self.run_script('title', settings=probe)
        if PROBE not in result.stdout:
            self.fail('ntfy_send.sh did not read NTFY_SECRETS_FILE; refusing to post anywhere')

    def route_to(self, url):
        self.settings.write_text(f'NTFY_URL={shlex.quote(url)}\nunset NTFY_TOPIC_VAR\n')

    def run_script(self, *args, settings=None):
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.scratch),
               'NTFY_SECRETS_FILE': str(settings or self.settings)}
        return subprocess.run(['/bin/bash', str(SCRIPT), *args], capture_output=True, text=True,
                              env=env, timeout=30, check=False)

    def test_message_starting_with_at_is_posted_literally_not_read_as_a_file(self):
        secret = self.scratch / 'fake-secret'
        secret.write_text('SECRET=1\n')
        for message in (f'@{secret}', '@no such file: reply line'):
            result = self.run_script('title', message)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.server.posts[-1][1], message)

    def test_title_and_fields_stay_one_header_each(self):
        result = self.run_script('first\r\nX-Injected: 1', 'body', 'high\nX-Other: 2', 'robot')
        self.assertEqual(result.returncode, 0, result.stderr)
        headers, body = self.server.posts[-1]
        self.assertEqual(body, 'body')
        self.assertEqual(headers['Title'], 'first  X-Injected: 1')
        self.assertEqual(headers['Priority'], 'high X-Other: 2')
        self.assertEqual(headers['Tags'], 'robot')
        self.assertIsNone(headers['X-Injected'])
        self.assertIsNone(headers['X-Other'])

    def test_failed_post_exits_one(self):
        self.route_to('http://127.0.0.1:9/unreachable')
        result = self.run_script('title', 'body')
        self.assertEqual(result.returncode, 1)
        self.assertIn('ntfy_send failed: title', result.stderr)
        self.assertEqual(self.server.posts, [])


if __name__ == '__main__':
    unittest.main()
