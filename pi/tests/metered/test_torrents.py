"""Exercise metering holds against a real loopback qBittorrent protocol server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from urllib.parse import parse_qs

from pi.apps.metered.config import FORCED_TAG, HOLD_TAG, POLICYCTL
from pi.apps.metered.torrent_api import TorrentApi, reconcile_api
from pi.apps.metered.torrents import TorrentHold
from shared.metered.records import Actual


class TorrentTests(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(hash='a' * 40, state='downloading', tags='keep'),
                     dict(hash='b' * 40, state='pausedDL', tags='manual')]
        self.version = 'v4.5.2'
        self.fail = False
        self.ignore_pause = False
        self.events = []
        case = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                value = json.dumps(case.rows).encode() if self.path.endswith('torrents/info') else case.version.encode()
                self.send_response(500 if case.fail else 200); self.end_headers(); self.wfile.write(value)
            def do_POST(self):
                endpoint = self.path.rsplit('/', 1)[-1]
                form = parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
                hashes = form['hashes'][0].split('|')
                case.events.append((endpoint, hashes))
                for row in case.rows:
                    if row['hash'] not in hashes:
                        continue
                    tags = set(filter(None, row['tags'].split(',')))
                    if endpoint == 'addTags':
                        tags.add(form['tags'][0])
                    elif endpoint == 'removeTags':
                        tags.discard(form['tags'][0])
                    elif endpoint == 'setForceStart':
                        row['force_start'] = form['value'][0] == 'true'
                    elif endpoint in ('pause', 'stop') and not case.ignore_pause:
                        row['state'] = 'pausedDL' if endpoint == 'pause' else 'stoppedDL'
                        row['force_start'] = False
                    elif endpoint in ('resume', 'start'):
                        row['state'] = 'downloading'
                    row['tags'] = ','.join(sorted(tags))
                self.send_response(200); self.end_headers()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.close_server)
        self.api = TorrentApi(base_url=f'http://127.0.0.1:{self.server.server_port}')

    def close_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def test_only_owned_torrents_resume_and_new_downloads_are_held(self):
        reconcile_api(self.api, False)
        self.assertEqual(self.rows[0]['state'], 'pausedDL')
        self.assertEqual(self.rows[1]['tags'], 'manual')
        self.assertEqual(self.events[0][0], 'addTags')
        self.rows.append(dict(hash='c' * 40, state='downloading', tags=''))
        reconcile_api(self.api, False)
        self.assertIn(HOLD_TAG, self.rows[2]['tags'])
        reconcile_api(self.api, True)
        self.assertEqual([row['state'] for row in self.rows], ['downloading', 'pausedDL', 'downloading'])
        self.assertEqual(self.rows[0]['tags'], 'keep')
        self.assertEqual(self.rows[1]['tags'], 'manual')
        self.assertFalse(any(HOLD_TAG in row['tags'] for row in self.rows))

    def test_force_started_torrents_keep_force_start_across_a_hold(self):
        self.rows[0]['force_start'] = True
        self.rows.append(dict(hash='c' * 40, state='uploading', tags='', force_start=False))
        reconcile_api(self.api, False)
        self.assertFalse(self.rows[0]['force_start'])
        reconcile_api(self.api, True)
        self.assertTrue(self.rows[0]['force_start'])
        self.assertFalse(self.rows[2]['force_start'])
        self.assertFalse(any(FORCED_TAG in row['tags'] or HOLD_TAG in row['tags'] for row in self.rows))

    def test_explicitly_unowned_hold_stays_manual_and_v5_uses_stop(self):
        self.version = 'v5.0.0'
        reconcile_api(self.api, False)
        self.assertEqual(self.rows[0]['state'], 'stoppedDL')
        self.rows[0]['tags'] = 'keep'
        reconcile_api(self.api, True)
        self.assertEqual(self.rows[0]['state'], 'stoppedDL')
        self.assertNotIn('pause', [event[0] for event in self.events])

    def test_unconfirmed_pause_falls_back_without_touching_live_processes(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            running = [True]
            commands = []
            def command(argv, **kwargs):
                commands.append(argv)
                if argv[0] == '/usr/bin/pgrep':
                    return subprocess.CompletedProcess(argv, 0 if running[0] else 1, '', '')
                if argv[0] == '/bin/bash':
                    running[0] = False
                    return subprocess.CompletedProcess(argv, 0, '', '')
                raise AssertionError(argv)
            self.ignore_pause = True
            hold = TorrentHold(directory, self.api, command,
                               lifecycle_lock=directory / 'lifecycle', ignition=directory / 'ignition')
            self.assertEqual(hold(False), Actual.HELD)
            self.assertTrue(hold.fallback.load().stopped_by_metering)
            self.assertEqual(sum(argv[0] == '/bin/bash' for argv in commands), 1)

    def test_approval_cannot_resume_when_disk_policy_forbids_it(self):
        reconcile_api(self.api, False)
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            def command(argv, **kwargs):
                if argv[0] == '/usr/bin/pgrep':
                    return subprocess.CompletedProcess(argv, 0, '', '')
                self.assertEqual(argv[0], POLICYCTL)
                return subprocess.CompletedProcess(argv, 0, json.dumps({
                    'disks_enabled': False, 'torrents_enabled': True,
                    'runtime': {'mounted_disk_labels': []}}), '')
            hold = TorrentHold(directory, self.api, command,
                               lifecycle_lock=directory / 'lifecycle', ignition=directory / 'ignition')
            self.assertEqual(hold(True, reset_connections=True), Actual.HELD)
            self.assertEqual(self.rows[0]['state'], 'pausedDL')


if __name__ == '__main__':
    unittest.main()
