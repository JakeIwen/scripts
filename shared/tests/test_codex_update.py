"""Weekly Codex update job: Starlink deferral, weekly wait, failures and the panel record."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import tool_automation

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('codex_update', REPO / 'shared/sh/codex_update.py')
codex_update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codex_update)

NOW = 1_800_000_000.0


def evidence(members, ubnt_ssid='Campground', wireless=None):
    return {'checked_at': NOW, 'router': {'route_members': members},
            'ubnt': {'connected': True, 'ssid': ubnt_ssid}, 'wireless_uplinks': wireless or {}}


class FakeMachine:
    """Answers the commands the job runs; `versions` is what `codex --version` reports in turn."""

    def __init__(self, uplink=None, gateway='192.168.6.1', pi=None, update_code=0,
                 versions=('0.163.0', '0.164.0')):
        self.uplink, self.gateway, self.pi = uplink, gateway, pi
        self.update_code, self.versions = update_code, list(versions)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[-1] == codex_update.PI_UPLINK_EVIDENCE:
            return self.reply(json.dumps(self.uplink))
        if argv[0] == '/sbin/route':
            return self.reply(f'   route to: default\n    gateway: {self.gateway}\n')
        if argv[0] == '/usr/bin/ssh':
            answer = self.pi(argv[argv.index(codex_update.PI_SCRIPT) + 1:]) if self.pi else None
            return self.reply(json.dumps(answer), 0 if answer is not None else 255)
        if argv[1:] == ['--version']:
            version = self.versions.pop(0) if len(self.versions) > 1 else self.versions[0]
            return self.reply(f'codex-cli {version}\n')
        if argv[1:] == ['update']:
            assert kwargs['env']['CODEX_NON_INTERACTIVE'] == '1'
            assert kwargs['env']['PATH'].startswith(str(Path(argv[0]).parent) + ':')
            return self.reply('installed\n', self.update_code)
        raise AssertionError(f'unexpected command {argv}')

    def reply(self, stdout, code=0):
        return subprocess.CompletedProcess([], code, stdout, '')

    def updated(self):
        return any(call[1:] == ['update'] for call in self.calls)


class CodexUpdateTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def host(self, machine, is_pi=True, now=NOW):
        return codex_update.Host(is_pi=is_pi, codex=self.root / 'bin/codex',
                                 state_dir=self.root / 'state', run=machine, now=lambda: now)

    def main(self, argv, host):
        output = io.StringIO()
        with redirect_stdout(output):
            code = codex_update.main(argv, host)
        return code, output.getvalue()

    def state(self):
        return json.loads((self.root / 'state/state.json').read_text())

    def test_pi_defers_while_the_antenna_is_on_starlink(self):
        machine = FakeMachine(evidence([{'name': 'wan', 'percent': 100}], ubnt_ssid='denlink'))
        code, output = self.main(['run'], self.host(machine))
        self.assertEqual((code, machine.updated()), (0, False))
        self.assertIn('deferred: wan is on Starlink', output)
        self.assertEqual(self.state()['outcome'], 'deferred')

    def test_only_the_exact_denlink_ssid_counts_as_starlink(self):
        for ssid in ('Starlink', 'DenLink', 'denlink-guest'):
            machine = FakeMachine(evidence([{'name': 'wan', 'percent': 100}], ubnt_ssid=ssid))
            self.main(['run', '--force'], self.host(machine))
            self.assertTrue(machine.updated(), ssid)

    def test_any_selected_starlink_member_defers_even_when_balanced(self):
        machine = FakeMachine(evidence([{'name': 'wan', 'percent': 50}, {'name': 'starwan', 'percent': 50}]))
        self.main(['run'], self.host(machine))
        self.assertFalse(machine.updated())
        self.assertEqual(self.state()['reason'], 'uplink starwan is selected')

    def test_unverifiable_uplink_defers(self):
        machine = FakeMachine({'error': 'uplink inspection failed'})
        self.main(['run'], self.host(machine))
        self.assertFalse(machine.updated())

    def test_updates_then_waits_a_week(self):
        machine = FakeMachine(evidence([{'name': 'clientwan', 'percent': 100}],
                                       wireless={'clientwan': {'up': True, 'ssid': 'lion_fone'}}))
        code, output = self.main(['run'], self.host(machine))
        self.assertEqual(code, 0)
        self.assertIn('updated: 0.163.0 -> 0.164.0', output)
        self.assertEqual(self.state()['last_success'], NOW)

        later = FakeMachine(evidence([{'name': 'wan', 'percent': 100}]))
        self.main(['run'], self.host(later, now=NOW + 6 * 86400))
        self.assertEqual(later.calls, [])
        self.main(['run'], self.host(later, now=NOW + 7 * 86400))
        self.assertTrue(later.updated())

    def test_force_skips_the_wait_but_not_the_uplink_check(self):
        good = FakeMachine(evidence([{'name': 'wan', 'percent': 100}]))
        self.main(['run'], self.host(good))
        starlink = FakeMachine(evidence([{'name': 'wan', 'percent': 100}], ubnt_ssid='denlink'))
        self.main(['run', '--force'], self.host(starlink, now=NOW + 60))
        self.assertFalse(starlink.updated())
        self.assertEqual(self.state()['last_success'], NOW)

    def test_failed_update_is_retried_next_check(self):
        machine = FakeMachine(evidence([{'name': 'wan', 'percent': 100}]), update_code=1,
                              versions=('0.163.0',))
        code, _ = self.main(['run'], self.host(machine))
        self.assertEqual(code, 1)
        self.assertEqual(self.state()['outcome'], 'failed')
        self.assertNotIn('last_success', self.state())

    def test_mac_off_the_van_lan_updates_without_asking_the_pi(self):
        machine = FakeMachine(gateway='10.0.0.1')
        self.main(['run'], self.host(machine, is_pi=False))
        self.assertTrue(machine.updated())
        self.assertFalse(any(call[0] == '/usr/bin/ssh' for call in machine.calls))

    def test_mac_on_the_van_lan_follows_the_pi_verdict(self):
        deferred = FakeMachine(pi=lambda args: {'allowed': False, 'reason': 'wan is on Starlink'})
        self.main(['run'], self.host(deferred, is_pi=False))
        self.assertFalse(deferred.updated())
        self.assertEqual(self.state()['reason'], 'wan is on Starlink')

        unreachable = FakeMachine(pi=None)
        self.main(['run'], self.host(unreachable, is_pi=False))
        self.assertFalse(unreachable.updated())
        self.assertEqual(self.state()['reason'], 'cannot ask vanpi about the uplink')

    def test_panel_record_covers_both_hosts(self):
        good = FakeMachine(evidence([{'name': 'wan', 'percent': 100}]))
        self.main(['run'], self.host(good))
        pi_state = {'outcome': 'deferred', 'reason': 'wan is on Starlink', 'version': '0.164.0',
                    'last_check': NOW, 'last_success': NOW - 20 * 86400}
        machine = FakeMachine(pi=lambda args: pi_state if args[0] == 'status' else None)
        _, output = self.main(['status', '--automation'], self.host(machine, is_pi=False))
        record = tool_automation.validate_status(json.loads(output))
        self.assertEqual(record['level'], 'warn')
        labels = [field['label'] for field in record['fields']]
        self.assertIn('Mac version', labels)
        self.assertIn('Pi last check', labels)

        _, output = self.main(['status', '--automation'], self.host(FakeMachine(pi=None), is_pi=False))
        self.assertEqual(json.loads(output)['level'], 'unknown')


class ContractTest(unittest.TestCase):
    def test_pi_crontab_runs_the_path_the_mac_asks(self):
        self.assertIn(codex_update.PI_SCRIPT + ' run', (REPO / 'pi/crontab_pi').read_text())

    def test_panel_declaration_loads(self):
        declaration = tool_automation.load_declaration('codex-update')
        self.assertIn(str(REPO / 'shared/sh/codex_update.py'), declaration.status.argv)


if __name__ == '__main__':
    unittest.main()
