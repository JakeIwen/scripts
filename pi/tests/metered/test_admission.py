"""A cloud approval bypasses cost only, and belongs to one cloud consumer."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from pi.apps.metered.admission import decide
from shared.metered.config import first_run_config
from shared.metered.records import Approval, ApprovalKind, HostPolicy


ROOT = Path(__file__).resolve().parents[3]


def evidence():
    return {'checked_at': 100, 'local_route': {'gateway': '192.168.6.1', 'dev': 'eth0',
        'prefsrc': '192.168.6.103'}, 'ipv6_defaults': [], 'rule_names': ['default_rule_v4'],
        'router': {'reachable': True, 'default_policy': 'balanced',
                   'route_members': [{'name': 'wan', 'percent': 100}]},
        'ubnt': {'reachable': True, 'connected': True, 'ssid_verified': 'denlink',
                 'ssid_configured': 'denlink'}}


class AdmissionTests(unittest.TestCase):
    def test_one_cloud_approval_does_not_authorize_the_other_or_unknown_evidence(self):
        policy = HostPolicy(approvals={'pi.icloud_backup':
            Approval(ApprovalKind.UNTIL, 200, None, 'test', 90)})
        raw = evidence()
        self.assertTrue(decide(raw, 100, policy=policy)[0])
        self.assertFalse(decide(raw, 100, 'pi.time_machine_cloud', policy=policy)[0])
        for mutate in (lambda data: data.update(checked_at=60),
                       lambda data: data.update(rule_names=['default_rule_v4', 'custom']),
                       lambda data: data.update(ipv6_defaults=[{}]),
                       lambda data: data['ubnt'].update(ssid_verified=None),
                       lambda data: data['local_route'].update(dev='tailscale0')):
            sample = evidence(); mutate(sample)
            self.assertFalse(decide(sample, 100, policy=policy)[0])

    def test_config_list_not_the_starlink_label_is_metering_policy(self):
        config = replace(first_run_config(), metered_ssids=['another-network'])
        self.assertTrue(decide(evidence(), 100, config=config, policy=HostPolicy())[0])
        raw = evidence(); raw['ubnt']['ssid_verified'] = 'another-network'
        self.assertFalse(decide(raw, 100, config=config, policy=HostPolicy())[0])

    def test_installed_admission_cli_accepts_json_and_does_not_write_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ, PYTHONPATH=str(ROOT), VANPI_METERED_HOME=directory,
                               PYTHONDONTWRITEBYTECODE='1')
            raw = evidence(); raw['checked_at'] = time.time()
            result = subprocess.run([sys.executable, '-P', '-B', '-m', 'pi.apps.metered',
                'admit', 'pi.icloud_backup'], input=json.dumps(raw), env=environment,
                cwd=directory, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['allowed'])
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
