"""Fail-closed remote setup/admission through the offsite CLI, with disposable files."""
import json
import os
import unittest
from unittest.mock import patch

from offsite_remote import RemoteRepository
from offsite_repository import Commands, Interrupted
from pi.tests.backup.offsite_remote_fixtures import RemoteFixture, SECRET


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.f = RemoteFixture()
        self.addCleanup(self.f.close)
        result = self.f.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.f.sources()

    def check(self):
        return self.f.run('check', '--remote', '--label', self.f.label)

    def test_check_uses_dedicated_admission_and_controlled_auth_without_mounting_target(self):
        before = len(self.f.calls())
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.f.calls()[before:]
        self.assertFalse(any(row[0] == 'mounts' for row in calls))
        restic = [row for row in calls if row[0] == 'restic']
        self.assertTrue(all('rest:http://100.64.0.10:8088/' in row for row in restic))
        self.assertNotIn(SECRET, str(restic) + result.stdout + result.stderr)
        evidence = json.loads((self.f.cfg.state / 'remote-check.json').read_text())
        self.assertEqual(evidence['result'], 'ok')
        self.assertGreater(evidence['duration_seconds'], 0)

    def test_remote_config_must_be_private_literal_tailscale_and_separate_secret(self):
        self.f.remote_config.chmod(0o644)
        self.assertNotEqual(self.check().returncode, 0)
        self.f.remote_config.chmod(0o600)
        record = json.loads(self.f.remote_config.read_text())
        for key, value in [('address', '192.168.1.2'), ('address', 'nas.example.com'),
                           ('port', True), ('transport_password', 'short')]:
            self.f.remote_config.write_text(json.dumps({**record, key: value}))
            self.assertNotEqual(self.check().returncode, 0)
        self.f.remote_config.unlink()
        self.assertNotEqual(self.check().returncode, 0)

    def test_invalid_remote_backup_is_recorded_and_notified_without_any_repository_write(self):
        self.f.remote_config.unlink()
        before = len(self.f.calls())
        result = self.f.run('backup', '--remote', '--label', self.f.label, '--only', 'borg')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)['result'], 'failed')
        calls = self.f.calls()[before:]
        self.assertEqual(sum(row[0] == 'notify' for row in calls), 1)
        self.assertFalse(any(row[0] == 'restic' for row in calls))

    def test_missing_remote_config_does_not_affect_local_v1(self):
        self.f.remote_config.unlink()
        result = self.f.run('backup', '--label', self.f.label, '--only', 'borg')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unknown_registered_identity_refuses_without_network_or_initialization(self):
        self.f.cfg.state.joinpath('repos.json').write_text('{"schema":1,"repos":{}}')
        before = len(self.f.calls())
        self.assertNotEqual(self.check().returncode, 0)
        self.assertEqual(self.f.calls()[before:], [])

    def test_metered_denial_unknown_and_offline_never_contact_repository(self):
        for name in ('denied', 'offline', 'wrong-route'):
            path = self.f.root / name
            path.touch()
            before = len(self.f.calls())
            result = self.check()
            self.assertEqual(result.returncode, 143, result.stderr)
            self.assertFalse(any(row[0] == 'restic' for row in self.f.calls()[before:]))
            self.assertEqual(json.loads((self.f.cfg.state / 'remote-check.json').read_text())['result'], 'failed')
            path.unlink()
        self.f.cfg.commands['metered'] = '/not/installed'
        self.f.config_file.write_text(json.dumps(__import__('dataclasses').asdict(self.f.cfg), default=str))
        self.assertEqual(self.check().returncode, 143)

    def test_running_guard_refuses_config_or_registration_loss(self):
        repo = RemoteRepository(self.f.cfg, Commands(self.f.cfg, self.f.locks()), self.f.label)
        with patch.dict(os.environ, self.f.env):
            repo.network_guard()
            original = self.f.remote_config.read_bytes()
            self.f.remote_config.unlink()
            with self.assertRaises(Interrupted):
                repo.network_guard()
            self.f.remote_config.write_bytes(original)
            self.f.remote_config.chmod(0o600)
            (self.f.cfg.state / 'repos.json').write_text('{"schema":1,"repos":{}}')
            with self.assertRaises(Interrupted):
                repo.network_guard()

    def test_decrypted_config_identity_mismatch_stops_before_backup(self):
        self.f.state['repo_id'] = 'f' * 64
        self.f.save()
        result = self.f.run('backup', '--remote', '--label', self.f.label, '--only', 'borg')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Unknown repository ID', result.stdout)
        self.assertFalse(any(row[0] == 'restic' and 'backup' in row for row in self.f.calls()))


if __name__ == '__main__':
    unittest.main()
