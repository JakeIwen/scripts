"""Public non-backup sync boundaries, using real rsync, flock and private trees."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import sys
import unittest

import backup_deploy_sync as scope
from pi.tests.backup import sync_transport_fixture as transport


class ScopeTests(unittest.TestCase):
    def test_failed_profile_discovery_never_emits_partial_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in transport.plan.SYNC_FILES:
                shutil.copyfile(transport.BACKUP / name, root / name)
            with (root / 'backup_deploy_plan.sh').open('a') as stream:
                stream.write('\nlocal_backup_plan() { [[ "$1" != pi ]] || return 9; '
                             'printf "pi/scripts/tool|/home/pi/scripts/tool|replace|pi:pi|750|new\\n"; }\n')
            result = subprocess.run([sys.executable, str(root / 'backup_deploy_sync.py'), 'excludes', 'scripts'],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')

    def test_exclusions_are_the_union_of_both_canonical_profiles(self):
        protected = scope.destinations()
        for kind, destination in scope.ROOTS.items():
            result = subprocess.run([sys.executable, str(transport.BACKUP / 'backup_deploy_sync.py'), 'excludes', kind],
                                    capture_output=True, text=True, check=True)
            actual = {Path(destination + line) for line in result.stdout.splitlines()}
            self.assertEqual(actual, {Path(path) for path in protected if Path(destination) in Path(path).parents})
            self.assertNotIn(Path('/home/pi/scripts/connectivity_status.py'), actual)


@unittest.skipUnless(sys.platform.startswith('linux'), 'real sync fencing requires Linux flock/fdinfo')
class PublicSyncTests(unittest.TestCase):
    def fixture(self, healthy=True):
        result = transport.make(self, healthy)
        self.addCleanup(transport.cleanup_stages, result[0].root)
        return result

    def test_healthy_and_unbootstrapped_hosts_keep_nonbackup_deployment(self):
        for healthy in (True, False):
            with self.subTest(healthy=healthy):
                fixture, repo, env, _host, protected = self.fixture(healthy)
                result = transport.run(repo, env)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                helper = fixture.live['scripts'] / 'connectivity_status.py'
                self.assertEqual(helper.read_bytes(), (transport.REPO / 'pi/scripts/connectivity_status.py').read_bytes())
                self.assertEqual(protected, {path: path.read_bytes() for path in protected})
                for kind in scope.ROOTS:
                    for relative in scope.scoped(kind, scope.destinations()):
                        self.assertFalse(os.path.lexists(fixture.root / 'outgoing' / kind / str(relative)))
                self.assertTrue((fixture.live['scripts'] / 'fixture.sh').exists())
                self.assertTrue((fixture.host_root / 'home/pi/.bashrc').exists())
                self.assertTrue((fixture.host_root / 'home/pi/.config/labwc/autostart').exists())
                events = (fixture.root / 'transport-events').read_text()
                self.assertIn('publication-blocked', events)
                self.assertIn('packages\n', events)
                self.assertIn('compute\n', events)
                self.assertIn('systemctl enable --now new.service', fixture.log.read_text())

    def test_unready_or_corrupt_hosts_refuse_before_unrelated_upload(self):
        fixture, repo, env, host, protected = self.fixture()
        original = host.ready.read_bytes()
        for fault in ('maintenance', 'pending', 'incomplete', 'malformed', 'unsafe'):
            with self.subTest(fault=fault):
                if fault in ('maintenance', 'pending'):
                    (host.state / fault).write_text('held\n')
                elif fault == 'incomplete':
                    host.ready.unlink()
                elif fault == 'malformed':
                    host.ready.write_text(json.dumps({'version': 1}))
                else:
                    host.ready.chmod(0o666)
                result = transport.run(repo, env)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((fixture.root / 'transport-events').exists())
                self.assertEqual(protected, {path: path.read_bytes() for path in protected})
                if fault in ('maintenance', 'pending'):
                    (host.state / fault).unlink()
                host.ready.write_bytes(original)
                host.ready.chmod(0o600)

    def test_readiness_recheck_closes_the_preflight_gap(self):
        fixture, repo, env, _host, _protected = self.fixture()
        env['SYNC_INJECT_PENDING'] = '1'
        result = transport.run(repo, env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('transaction is pending', result.stderr)
        self.assertFalse((fixture.root / 'transport-events').exists())
        self.assertFalse(fixture.log.exists())


if __name__ == '__main__':
    unittest.main()
