"""Admission keeps selected-route checks without unrelated dashboard probes."""
import ast
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import icloud_uplink
from icloud_uplink import collect, decide as admission_command
from pi.tests.backup.metered_fixtures import decide


class CollectorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        home = mock.patch('icloud_uplink.Path.home', return_value=Path(temp.name))
        home.start(); self.addCleanup(home.stop)

    def collect(self, members, ssid='SafeWifi', fail_antenna=False):
        calls = []
        def command(args, timeout):
            calls.append(args)
            if args[:3] == ['/usr/bin/ip', '-j', '-6']:
                output = '[]'
            elif args[0] == '/usr/bin/ip':
                output = json.dumps([{'gateway': '192.168.6.1', 'dev': 'eth0', 'prefsrc': '192.168.6.103'}])
            elif 'iwconfig ath0' in args[-1]:
                if fail_antenna:
                    raise subprocess.TimeoutExpired(args, timeout)
                output = f'ath0 ESSID:"{ssid}" Access Point: 00:11:22:33:44:55\ntarget_ssid={ssid}\n'
            else:
                output = ('__VAN_DASH_DEFAULT_POLICY__=balanced\nCurrent ipv4 policies:\nbalanced:\n'
                          + ''.join(f' {name} ({weight}%)\n' for name, weight in members)
                          + 'Current ipv6 policies:\nRULE default_rule_v4\nUP clientwan true\n'
                          + 'RADIO clientwan {"ssid":"PhoneHotspot","bssid":"00:11:22:33:44:55","mode":"Client"}\n'
                          + 'UP lifiwan false\n')
            return subprocess.CompletedProcess(args, 0, output, '')
        return collect(command, lambda: 1000), calls

    def test_unused_antenna_is_not_queried(self):
        evidence, calls = self.collect([('clientwan', 100)], fail_antenna=True)
        self.assertTrue(decide(evidence, now=1000)[0])
        self.assertEqual(len(calls), 3)

    def test_every_selected_antenna_is_checked_and_starlink_blocked(self):
        for members in ([('wan', 100)], [('wan', 50), ('clientwan', 50)]):
            for ssid, allowed in [('SafeWifi', True), ('denlink', False), ('Starlink Mini', True)]:
                evidence, calls = self.collect(members, ssid)
                self.assertEqual(decide(evidence, now=1000)[0], allowed)
                self.assertEqual(evidence['ubnt']['starlink'], not allowed)
                self.assertEqual(len(calls), 4)

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

    def test_invalid_admission_response_is_a_denial_not_an_exception(self):
        for response in ([], {}, {'allowed': 1, 'reason': 'bad'}, {'allowed': True, 'reason': []}):
            def command(args, **kwargs):
                self.assertEqual(args[-2:], ['admit', 'pi.time_machine_cloud'])
                self.assertEqual(json.loads(kwargs['input']), {'sample': True})
                return subprocess.CompletedProcess(args, 0, json.dumps(response), '')
            self.assertFalse(admission_command({'sample': True}, 'pi.time_machine_cloud', command)[0])

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


def evidence():
    return {'checked_at': 1000, 'local_route': {'gateway': '192.168.6.1', 'dev': 'eth0', 'prefsrc': '192.168.6.103'},
            'router': {'reachable': True, 'default_policy': 'balanced', 'route_members': [{'name': 'wan', 'percent': 100}]},
            'rule_names': ['default_rule_v4'], 'ipv6_defaults': [],
            'ubnt': {'reachable': True, 'connected': True, 'ssid_verified': 'FriendsWifi'},
            'wireless_uplinks': {}}


class ReasonClassificationTests(unittest.TestCase):
    """Unknown verdicts hold; a verified metered verdict defers at once."""

    def reason(self, mutate, now=1000):
        value = evidence(); mutate(value)
        allowed, reason = decide(value, now)
        self.assertFalse(allowed)
        return reason

    def test_every_unclassifiable_verdict_is_a_hold_reason(self):
        cases = {
            'stale': lambda e: e.update(checked_at=900),
            'not online': lambda e: e['ubnt'].update(connected=False),
            'identity unknown': lambda e: e['ubnt'].update(ssid_verified=None),
            'incomplete policy': lambda e: e['router'].update(route_members=[]),
            'vpn route': lambda e: e['local_route'].update(dev='tailscale0'),
            'source rules': lambda e: e.update(rule_names=['default_rule_v4', 'custom']),
            'ipv6': lambda e: e.update(ipv6_defaults=[{'dst': 'default'}]),
            'router unreachable': lambda e: e['router'].update(reachable=False),
            'unknown member': lambda e: e['router'].update(route_members=[{'name': 'vpnwan', 'percent': 100}]),
            'joined': lambda e: (e.update(ipv6_defaults=[{}]), e['router'].update(reachable=False)),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                self.assertTrue(icloud_uplink.uplink_unknown(self.reason(mutate)))
        self.assertTrue(icloud_uplink.uplink_unknown(decide({}, 1000)[1]))

    def test_verified_metered_and_starlink_verdicts_defer_now(self):
        starlink = self.reason(lambda e: e['ubnt'].update(ssid_verified='denlink'))
        self.assertEqual(starlink, 'held on a metered or unknown network')
        self.assertFalse(icloud_uplink.uplink_unknown(starlink))

    def test_partly_known_or_malformed_reasons_are_not_holds(self):
        for reason in ('router is unreachable; held on a metered or unknown network', '', None,
                       'selected uplink identity is unknown;'):
            self.assertFalse(icloud_uplink.uplink_unknown(reason))


REPO = Path(__file__).resolve().parents[3]
# Every module whose literals can reach a Pi cloud worker as an admission refusal.
PRODUCERS = ('shared/metered/rules.py', 'shared/metered/evidence.py', 'shared/metered/admission.py',
             'pi/apps/metered/admission.py', 'pi/scripts/backup/icloud_uplink.py',
             'pi/scripts/backup/icloud_backup.py')
DEFER_NOW = {'held on a metered or unknown network'}
ALLOWED = {'unmetered', 'temporary approval', 'not gated (explicit exemption)'}
KNOWN_VERDICTS = {'metered SSID selected', 'all selected uplinks are unmetered'}
# rules.mac_verdict runs only on the Mac; the Pi cloud admission never emits these.
MAC_ONLY = {'Mac network is disconnected', 'Mac SSID is unreadable away from the verified home network',
            'Mac joined a metered SSID', 'Mac SSID is not metered'}
# The only non-literal reason forms a producer may use: the joined normalize()
# reasons, admission's pass-through of a verdict's reason, and unknown()'s own
# Verdict(None, reason) that wraps the literals collected above.
ALLOWED_DYNAMIC = {"'; '.join(evidence.unsupported)", 'verdict.reason', 'reason'}


class ReasonCoverageTests(unittest.TestCase):
    """Structural: a new refusal string cannot silently miss UNKNOWN_UPLINK_REASONS."""

    def emitted(self):
        literals, dynamic = set(), set()
        def take(node):
            if isinstance(node, ast.IfExp):
                take(node.body); take(node.orelse)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value)
            else:
                dynamic.add(ast.unparse(node))
        for relative in PRODUCERS:
            tree = ast.parse((REPO / relative).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = ast.unparse(node.func)
                    if name == 'unknown' and node.args:
                        take(node.args[0])
                    elif name == 'unsupported.append':
                        take(node.args[0])
                    elif name == 'Verdict' and len(node.args) > 1:
                        take(node.args[1])
                elif isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
                    items = node.value.elts
                    if len(items) == 2 and isinstance(items[0], ast.Constant) and items[0].value is False:
                        take(items[1])
                elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple):
                    items = node.value.elts
                    for index, item in enumerate(items[:-1]):
                        if isinstance(item, ast.Constant) and item.value is False:
                            take(items[index + 1])
                elif (isinstance(node, ast.Assign) and len(node.targets) == 1
                      and ast.unparse(node.targets[0]) == 'reason'):
                    take(node.value)
        return literals, dynamic

    def test_every_producer_literal_is_classified(self):
        literals, dynamic = self.emitted()
        classified = icloud_uplink.UNKNOWN_UPLINK_REASONS | DEFER_NOW | ALLOWED | KNOWN_VERDICTS | MAC_ONLY
        self.assertEqual(literals - classified, set(), 'classify new refusal reasons in icloud_uplink.py')
        self.assertEqual(dynamic - ALLOWED_DYNAMIC, set(), 'unrecognized refusal emission form')
        for group in (DEFER_NOW, ALLOWED, KNOWN_VERDICTS, MAC_ONLY):
            self.assertFalse(group & icloud_uplink.UNKNOWN_UPLINK_REASONS)

    def test_every_hold_reason_still_has_a_producer(self):
        literals, _ = self.emitted()
        self.assertEqual(icloud_uplink.UNKNOWN_UPLINK_REASONS - literals, set())
