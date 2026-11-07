"""Network rendering reads local evidence only; failed discovery is never 'missing'."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from offsite_availability import remote_availability
from offsite_config import Config
from pi.tests.backup.offsite_fixtures import core_limits_after


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.cfg = Config(borg=Path(root.name) / 'borg', ignition=Path(root.name) / 'ignition',
                          state=Path(root.name), owner=os.getuid())
        self.path = self.cfg.state / 'remote.json'
        self.path.write_text(json.dumps({'schema': 1, 'address': '100.64.0.10', 'port': 8088,
                                        'transport_password': 'x' * 48}))
        self.path.chmod(0o600)

    def command(self, online=True, route='tailscale0'):
        values = [json.dumps({'BackendState': 'Running', 'Self': {'Online': True},
                  'Peer': {'nas': {'Online': online, 'TailscaleIPs': ['100.64.0.10']}}}),
                  json.dumps([{'dev': route}])]
        return Mock(side_effect=[subprocess.CompletedProcess([], 0, value, '') for value in values])

    def test_online_peer_is_not_proof_of_receiver_or_disk_and_never_reads_restic(self):
        command = self.command()
        self.assertEqual(remote_availability(self.cfg, command=command),
                         {'state': 'unknown', 'reason': 'target-unverified'})
        self.assertEqual([call.args[0][0] for call in command.call_args_list],
                         ['/usr/bin/tailscale', '/usr/bin/ip'])
        self.assertTrue(all(call.kwargs['timeout'] == 2 for call in command.call_args_list))
        self.assertNotIn('x' * 48, str(command.call_args_list))

    def test_offline_or_wrong_route_is_unavailable(self):
        for command in (self.command(online=False), self.command(route='eth0')):
            self.assertEqual(remote_availability(self.cfg, command=command)['state'], 'unavailable')

    def test_unconfigured_and_unknown_are_distinct_and_do_not_probe_network(self):
        command = Mock()
        self.path.unlink()
        self.assertEqual(remote_availability(self.cfg, command=command)['reason'], 'not-configured')
        command.assert_not_called()
        self.path.write_text('invalid')
        self.path.chmod(0o600)
        self.assertEqual(remote_availability(self.cfg, command=command)['state'], 'unknown')
        command.assert_not_called()

    def test_incomplete_or_new_tailscale_states_are_unknown_not_offline(self):
        complete = {'BackendState': 'Running', 'Self': {'Online': True},
                    'Peer': {'nas': {'Online': True, 'TailscaleIPs': ['100.64.0.10']}}}
        invalid = [{'Peer': {}}, {'BackendState': 'Starting', 'Peer': None},
                   {**complete, 'BackendState': 'new-state'},
                   {**complete, 'Self': {'Online': 'true'}},
                   {**complete, 'Peer': {'nas': {'Online': True, 'TailscaleIPs': '100.64.0.10'}}}]
        for status in invalid:
            with self.subTest(status=status):
                command = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(status), ''))
                self.assertEqual(remote_availability(self.cfg, command=command),
                                 {'state': 'unknown', 'reason': 'probe-failed'})
                self.assertEqual(command.call_count, 1)
        for state in ('Stopped', 'NeedsLogin', 'NeedsMachineAuth'):
            for peers in ({}, None):
                payload = {'BackendState': state, 'Peer': peers}
                command = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), ''))
                self.assertEqual(remote_availability(self.cfg, command=command)['reason'], 'network-offline')
                self.assertEqual(command.call_count, 1)

    def test_incomplete_route_evidence_is_unknown_not_offline(self):
        for route in (None, '', False):
            self.assertEqual(remote_availability(self.cfg, command=self.command(route=route)),
                             {'state': 'unknown', 'reason': 'probe-failed'})

    def test_failed_command_or_malformed_evidence_remains_unknown(self):
        for command in (Mock(side_effect=OSError()), Mock(side_effect=subprocess.TimeoutExpired('test', 2)),
                        Mock(return_value=subprocess.CompletedProcess([], 1, '', '')),
                        Mock(return_value=subprocess.CompletedProcess([], 0, '{}', ''))):
            self.assertEqual(remote_availability(self.cfg, command=command),
                             {'state': 'unknown', 'reason': 'probe-failed'})

    def test_reading_the_transport_disables_core_dumps_in_a_fresh_process(self):
        # remote.json can hold the DSM password; the dashboard path reads it with no other protection.
        config = self.cfg.state / 'config.json'
        config.write_text(json.dumps(asdict(self.cfg), default=str))
        body = ('from offsite_availability import remote_availability\n'
                'import subprocess\n'
                'remote_availability(cfg, command=lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))')
        before, after = core_limits_after(config, body)
        self.assertNotEqual(before, (0, 0))
        self.assertEqual(after, (0, 0))


if __name__ == '__main__':
    unittest.main()
