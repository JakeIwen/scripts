"""Admission keeps selected-route checks without unrelated dashboard probes."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from icloud_uplink import collect, decide


class CollectorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        home = mock.patch('icloud_uplink.Path.home', return_value=Path(temp.name))
        home.start(); self.addCleanup(home.stop)

    def collect(self, members, ssid='SafeWifi', fail_antenna=False):
        calls = []
        def command(args, timeout):
            calls.append(args)
            if args[0] == '/usr/bin/ip':
                output = json.dumps([{'gateway': '192.168.6.1', 'dev': 'eth0', 'prefsrc': '192.168.6.103'}])
            elif args[-1] == '/sbin/iwconfig ath0':
                if fail_antenna:
                    raise subprocess.TimeoutExpired(args, timeout)
                output = f'ath0 ESSID:"{ssid}" Access Point: 00:11:22:33:44:55'
            else:
                output = ('__VAN_DASH_DEFAULT_POLICY__=balanced\nCurrent ipv4 policies:\nbalanced:\n'
                          + ''.join(f' {name} ({weight}%)\n' for name, weight in members)
                          + 'Current ipv6 policies:\nRULE default_rule_v4\nUP clientwan true\n'
                          + 'SSID clientwan PhoneHotspot\nUP lifiwan false\n')
            return subprocess.CompletedProcess(args, 0, output, '')
        return collect(command, lambda: 1000), calls

    def test_unused_antenna_is_not_queried(self):
        evidence, calls = self.collect([('clientwan', 100)], fail_antenna=True)
        self.assertTrue(decide(evidence, ['denlink'], now=1000)[0])
        self.assertEqual(len(calls), 2)

    def test_every_selected_antenna_is_checked_and_starlink_blocked(self):
        for members in ([('wan', 100)], [('wan', 50), ('clientwan', 50)]):
            for ssid, allowed in [('SafeWifi', True), ('denlink', False), ('Starlink Mini', False)]:
                evidence, calls = self.collect(members, ssid)
                self.assertEqual(decide(evidence, ['denlink'], now=1000)[0], allowed)
                self.assertEqual(len(calls), 3)

    def test_selected_antenna_failure_is_not_ignored(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.collect([('wan', 100)], fail_antenna=True)

    def test_failed_route_or_router_probe_fails_closed(self):
        for failed in ('/usr/bin/ip', '/usr/bin/ssh'):
            def command(args, timeout):
                return subprocess.CompletedProcess(args, 1 if args[0] == failed else 0,
                    '[{"gateway":"192.168.6.1"}]', '')
            with self.assertRaises(subprocess.CalledProcessError):
                collect(command)

    def test_shared_connection_directory_must_be_private_and_not_a_symlink(self):
        from icloud_uplink import probe_ssh_args
        directory = Path.home() / '.vanpi-icloud-uplink'
        directory.mkdir(mode=0o755)
        with self.assertRaises(ValueError): probe_ssh_args('unused')
        directory.rmdir()
        directory.symlink_to(Path.home(), target_is_directory=True)
        with self.assertRaises(ValueError): probe_ssh_args('unused')


if __name__ == '__main__':
    unittest.main()
