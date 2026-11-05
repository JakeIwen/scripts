"""Opt-in real restic/rest-server loopback protocol, never live sources or ports.

Set OFFSITE_RESTIC and OFFSITE_REST_SERVER to isolated archive-verified binaries.
The default suite skips this external-binary acceptance test explicitly.
"""
import base64
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import unittest
import urllib.error
import urllib.request

from offsite_config import INCOMPLETE_TAG, RECEIPT_TAG
from pi.tests.backup.offsite_remote_fixtures import RemoteFixture, SECRET
from pi.tests.backup.offsite_state_writes import assert_stdin_consumed

RESTIC = os.environ.get('OFFSITE_RESTIC')
SERVER = os.environ.get('OFFSITE_REST_SERVER')


class AdmissionFixtureTests(unittest.TestCase):
    def test_partition_double_waits_for_input(self):
        fixture = RemoteFixture()
        self.addCleanup(fixture.close)
        assert_stdin_consumed(fixture, 'sfdisk')

    def test_metered_double_requires_complete_json_evidence(self):
        fixture = RemoteFixture()
        self.addCleanup(fixture.close)
        for evidence, valid in (('', False), ('{', False), ('{}', True)):
            with self.subTest(evidence=evidence):
                result = subprocess.run([str(fixture.bin / 'metered'), 'admit', 'pi.offsite_backup'],
                                        input=evidence, env=fixture.env, capture_output=True,
                                        text=True, timeout=5)
                self.assertEqual(result.returncode == 0, valid, result.stdout + result.stderr)
                if valid:
                    self.assertIs(json.loads(result.stdout)['allowed'], True)


@unittest.skipUnless(RESTIC and SERVER, 'opt-in acceptance: set isolated pinned OFFSITE_RESTIC and OFFSITE_REST_SERVER binaries')
class RestProtocolTests(unittest.TestCase):
    def setUp(self):
        self.assertIn('0.19.1', subprocess.check_output([RESTIC, 'version'], text=True))
        self.assertIn('0.14.0', subprocess.check_output([SERVER, '--version'], text=True))
        self.f = RemoteFixture()
        self.addCleanup(self.f.close)
        self.assertEqual(self.f.prepare().returncode, 0)
        self.f.sources()
        self.repo = self.f.root / 'repo-A'
        self.server = None
        self.addCleanup(self.stop_server)
        self.repo_id = self.init_repo(self.repo)
        marker = self.f.marker()
        marker['repo_id'] = self.repo_id
        known = self.f.cfg.state / 'repos.json'
        known.write_text(json.dumps({'schema': 1, 'repos': {self.f.label: marker}}))
        self.f.cfg.commands['restic'] = RESTIC
        self.f.config_file.write_text(json.dumps(asdict(self.f.cfg), default=str))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.url = 'http://127.0.0.1:{}/'.format(self.port)
        self.f.loopback = 'rest:' + self.url
        self.start_server(self.repo, self.repo_id)

    def restic(self, repo, *args, check=True):
        env = {key: value for key, value in os.environ.items() if not key.startswith('RESTIC_')}
        result = subprocess.run([RESTIC, '--repo', str(repo), '--password-file', str(self.f.cfg.password),
                                 '--no-cache', *args], env=env, capture_output=True, text=True, timeout=30)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def init_repo(self, path):
        self.restic(path, 'init')
        return json.loads(self.restic(path, 'cat', 'config').stdout)['id']

    def start_server(self, repo, repo_id):
        self.stop_server()
        auth = self.f.root / 'fixture.htpasswd'
        auth.write_text(repo_id + ':{SHA}' + base64.b64encode(hashlib.sha1(SECRET.encode()).digest()).decode() + '\n')
        auth.chmod(0o600)
        self.server = subprocess.Popen([SERVER, '--path', str(repo), '--listen', '127.0.0.1:' + str(self.port),
                                        '--htpasswd-file', str(auth), '--append-only'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                self.request(repo_id, 'config')
                return
            except OSError:
                if self.server.poll() is not None:
                    self.fail('rest-server exited before loopback readiness')
                time.sleep(0.02)
        self.fail('rest-server did not become ready')

    def stop_server(self):
        if self.server is not None:
            self.server.terminate()
            try:
                self.server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait()
            self.server = None

    def request(self, username, path, data=None):
        request = urllib.request.Request(self.url + path, data=data)
        request.add_header('Authorization', 'Basic ' + base64.b64encode((username + ':' + SECRET).encode()).decode())
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=2) as response:
            return response.read()

    def backup(self, source='borg'):
        return self.f.run('backup', '--remote', '--label', self.f.label, '--only', source)

    def test_backup_receipt_check_restore_and_append_only_refusal(self):
        self.f.env.update(RESTIC_PASSWORD='wrong ambient encryption password',
                          RESTIC_REST_USERNAME='wrong-ambient-user', RESTIC_REST_PASSWORD='wrong-ambient-auth',
                          HTTP_PROXY='http://127.0.0.1:1', http_proxy='http://127.0.0.1:1')
        result = self.backup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        outcome = json.loads(result.stdout)['sources']['borg']
        self.assertEqual(outcome['logical_bytes_processed'], len('content'))
        self.assertIsInstance(outcome['newly_added_packed_bytes'], int)
        listed = self.f.run('receipts', '--label', self.f.label)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        receipt = json.loads(listed.stdout)[0]
        self.assertEqual(receipt['snapshot'], outcome['snapshot'])
        self.assertEqual(receipt['receipt'], outcome['receipt'])
        snapshots = json.loads(self.restic(self.repo, 'snapshots', '--json').stdout)
        self.assertEqual(len(snapshots), 2)
        source = next(row for row in snapshots if row['id'] == outcome['snapshot'])
        self.assertIn(INCOMPLETE_TAG, source['tags'])
        self.assertEqual(next(row for row in snapshots if row['id'] == outcome['receipt'])['tags'], [RECEIPT_TAG])
        check = self.f.run('check', '--remote', '--label', self.f.label, '--read-data-subset', '100%')
        self.assertEqual(check.returncode, 0, check.stderr)
        restore = self.f.root / 'restored'
        restore.mkdir()
        sample = self.f.cfg.borg / 'sample'
        restored = self.f.run('restore-test', '--label', self.f.label, '--receipt', outcome['receipt'],
                              '--path', str(sample), '--target', str(restore))
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
        self.assertEqual((restore / str(sample).lstrip('/')).read_text(), 'content')
        for operation in ('check', 'restore-test'):
            evidence = json.loads((self.f.cfg.state / ('remote-' + operation + '.json')).read_text())
            self.assertEqual(evidence['result'], 'ok')
            self.assertGreater(evidence['duration_seconds'], 0)
        # Pinned restic reports forbidden tag deletion but (surprisingly) exits
        # zero. Completion therefore never relies on a tag command or exit alone.
        env = {**os.environ, 'RESTIC_REST_USERNAME': self.repo_id, 'RESTIC_REST_PASSWORD': SECRET}
        tagged = subprocess.run([RESTIC, '--repo', self.f.loopback, '--password-file', str(self.f.cfg.password),
                                 '--no-cache', 'tag', '--remove', INCOMPLETE_TAG, outcome['snapshot']],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertIn('403', tagged.stderr)
        self.assertIn('no snapshots were modified', tagged.stdout)
        self.assertTrue((self.repo / 'snapshots' / outcome['snapshot']).exists())

    def test_switch_fences_old_authenticated_writes_before_any_client_guard(self):
        other = self.f.root / 'repo-B'
        other_id = self.init_repo(other)
        self.start_server(other, other_id)
        before = sorted(str(path.relative_to(other)) for path in other.rglob('*'))
        with self.assertRaises(urllib.error.HTTPError) as refused:
            self.request(self.repo_id, 'data/' + 'a' * 64, b'old client write')
        self.assertEqual(refused.exception.code, 401)
        self.assertEqual(sorted(str(path.relative_to(other)) for path in other.rglob('*')), before)
        self.assertTrue(self.request(other_id, 'config'))

    def test_interrupted_backup_has_no_receipt_and_rechecks_admission_during_upload(self):
        sample = self.f.cfg.borg / 'sample'
        sample.write_bytes(os.urandom(2 * 1024 * 1024))
        wrapper = self.f.bin / 'slow-restic'
        wrapper.write_text('#!' + __import__('sys').executable + '\nimport os,sys\nos.execv(' + repr(RESTIC)
                           + ',[' + repr(RESTIC) + ',"--limit-upload","64",*sys.argv[1:]])\n')
        wrapper.chmod(0o700)
        self.f.cfg.commands['restic'] = str(wrapper)
        self.f.config_file.write_text(json.dumps(asdict(self.f.cfg), default=str))
        child = self.f.start('backup', '--remote', '--label', self.f.label, '--only', 'borg')
        try:
            deadline = time.monotonic() + 20
            while not list((self.repo / 'locks').iterdir()) and child.poll() is None:
                self.assertLess(time.monotonic(), deadline, 'backup did not acquire its real restic lock')
                time.sleep(0.02)
            (self.f.root / 'denied').touch()
            out, err = child.communicate(timeout=25)
            self.assertEqual(child.returncode, 143, out + err)
            self.assertIn('admission denied or unknown', out)
            self.assertFalse(any(RECEIPT_TAG in row.get('tags', []) for row in
                                 json.loads(self.restic(self.repo, 'snapshots', '--json').stdout)))
            self.assertEqual(json.loads(out)['result'], 'interrupted')
        finally:
            if child.poll() is None:
                child.terminate()
                child.communicate(timeout=20)

    def test_changed_time_machine_is_never_receipted(self):
        # Simulate another writer despite the Samba gate, using a real restic
        # wrapper that changes the fixture image after backup returns successfully.
        script = self.f.bin / 'change-image'
        image = self.f.cfg.sources()['time-machine'].paths[0] / 'sample'
        script.write_text('#!' + __import__('sys').executable + '\nimport subprocess,sys\nfrom pathlib import Path\n'
                          + 'p=subprocess.run([' + repr(RESTIC) + ',*sys.argv[1:]])\n'
                          + 'if "backup" in sys.argv: Path(' + repr(str(image)) + ').write_text("changed")\n'
                          + 'raise SystemExit(p.returncode)\n')
        script.chmod(0o700)
        self.f.cfg.commands['restic'] = str(script)
        self.f.config_file.write_text(json.dumps(asdict(self.f.cfg), default=str))
        result = self.backup('time-machine')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('image changed during backup', result.stdout)
        snapshots = json.loads(self.restic(self.repo, 'snapshots', '--json').stdout)
        self.assertEqual(len(snapshots), 1)
        self.assertIn(INCOMPLETE_TAG, snapshots[0]['tags'])
        self.assertNotIn(RECEIPT_TAG, snapshots[0]['tags'])
        self.assertFalse(self.f.cfg.drain.exists())


if __name__ == '__main__':
    unittest.main()
