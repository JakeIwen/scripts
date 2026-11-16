"""Worker-path uplink holds through the real guard, run() and manual entry points."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

import icloud_backup as backup
import icloud_uplink_hold
from pi.tests.backup.metered_fixtures import decide

REPO = Path(__file__).resolve().parents[3]
UNKNOWN = 'selected uplink identity is unknown'


def evidence(ssid='FriendsWifi'):
    return {'checked_at': 1000, 'local_route': {'gateway': '192.168.6.1', 'dev': 'eth0', 'prefsrc': '192.168.6.103'},
            'router': {'reachable': True, 'default_policy': 'balanced', 'route_members': [{'name': 'wan', 'percent': 100}]},
            'rule_names': ['default_rule_v4'], 'ipv6_defaults': [],
            'ubnt': {'reachable': True, 'connected': True, 'ssid_verified': ssid},
            'wireless_uplinks': {}}


OK, LOST, METERED = evidence(), evidence(None), evidence('denlink')


class Child:
    """A client that runs until killed, or exits with `rc` after `polls` polls."""
    stdout = None
    pids = iter(range(424242, 425242))

    def __init__(self, rc=None, polls=0):
        self.returncode, self.rc, self.polls = None, rc, polls
        self.pid = next(Child.pids)

    def poll(self):
        if self.returncode is None and self.rc is not None:
            if self.polls <= 0:
                self.returncode = self.rc
            self.polls -= 1
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = -15
        return self.returncode


class WorkerHoldTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.flag = self.root / 'ignition'
        self.policy = '1 1 1'
        self.verdicts, self.probes = [], []
        self.enterContext(mock.patch.dict(os.environ, {'VANPI_ICLOUD_IGNITION_FLAG': str(self.flag)}))
        self.enterContext(mock.patch.object(icloud_uplink_hold, 'RECHECK_SECONDS', 0.05))
        self.enterContext(mock.patch.object(backup, 'capture', side_effect=self.capture))
        self.enterContext(mock.patch.object(backup, 'decide', side_effect=lambda value, consumer_id:
                                            decide(value, now=1000, consumer_id=consumer_id)))
        self.enterContext(mock.patch.object(backup.priority, 'policy', return_value=backup.priority.PriorityMode.NORMAL))
        self.enterContext(mock.patch.object(backup.admission, 'client_fds', return_value=()))
        self.kill = self.enterContext(mock.patch.object(backup.os, 'kill'))
        self.killpg = self.enterContext(mock.patch.object(backup.os, 'killpg'))
        self.enterContext(mock.patch('builtins.print'))
        self.reports = []
        icloud_uplink_hold.set_reporter(self.reports.append)
        self.addCleanup(icloud_uplink_hold.set_reporter, None)
        self.cfg = {**json.loads((REPO / 'pi/configs/icloud-backup.json').read_text()),
                    'guard_interval_seconds': 0.01, 'uplink_unknown_grace_seconds': 2,
                    'no_progress_timeout_seconds': 30}

    def capture(self, args, timeout=None):
        if args[0] == '/home/pi/scripts/policyctl':
            return self.policy
        self.probes.append(time.monotonic())
        step = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
        if callable(step):
            step = step()
        return json.dumps(step)

    def run_with(self, children, operation='copy', **kwargs):
        with mock.patch.object(backup.subprocess, 'Popen', side_effect=children) as popen:
            try:
                return backup.run([backup.RCLONE, operation, 'a', 'b'], self.cfg, network=True, **kwargs)
            finally:
                self.popen = popen

    def signals(self, sig):
        return [c for c in self.kill.call_args_list + self.killpg.call_args_list if c.args[1] == sig]

    def test_no_client_starts_while_the_verdict_stays_unknown(self):
        self.verdicts = [LOST]
        started = time.monotonic()
        with self.assertRaisesRegex(backup.UplinkUnknown, UNKNOWN):
            self.run_with([Child(0)])
        self.popen.assert_not_called()
        self.assertTrue(all(probe < started + 2 for probe in self.probes))
        self.assertGreater(len(self.probes), 2)
        self.assertIsNone(self.reports[-1])
        self.assertTrue(all(r['reason'] == UNKNOWN for r in self.reports if r))

    def test_hold_recovers_and_rechecks_parking_after_the_final_probe(self):
        self.verdicts = [LOST, LOST, OK]
        with mock.patch.object(backup, 'check_work_allowed', wraps=backup.check_work_allowed) as checks:
            self.assertEqual(self.run_with([Child(0)]), '')
        self.popen.assert_called_once()
        # Before the guard, between probes, after the final probe, then nothing else.
        self.assertGreaterEqual(checks.call_count, 3)

    def test_ignition_or_hdd_policy_change_during_the_final_probe_starts_nothing(self):
        for name, change in (('ignition', lambda: self.flag.touch()),
                             ('policy', lambda: setattr(self, 'policy', '0 1 1'))):
            with self.subTest(name):
                self.flag.unlink(missing_ok=True); self.policy = '1 1 1'; self.probes.clear()
                def final():
                    change()
                    return OK
                self.verdicts = [LOST, final]
                with self.assertRaises(backup.Deferred) as caught:
                    self.run_with([Child(0)])
                self.assertNotIsInstance(caught.exception, backup.UplinkUnknown)
                self.popen.assert_not_called()

    def test_priority_yield_during_a_hold_defers_at_once(self):
        self.cfg['priority_job'] = backup.priority.PriorityMode.TIME_MACHINE
        policies = iter([backup.priority.PriorityMode.NORMAL, backup.priority.PriorityMode.PI])
        self.verdicts = [LOST]
        with mock.patch.object(backup.priority, 'policy', side_effect=lambda: next(policies)):
            with self.assertRaisesRegex(backup.Deferred, '^yielding to selected'):
                self.run_with([Child(0)])
        self.assertEqual(len(self.probes), 1)

    def test_a_running_client_is_killed_at_its_first_unknown_verdict_then_restarted(self):
        first, second = Child(), Child(0, polls=2)
        self.cfg['no_progress_timeout_seconds'] = 1
        # Unknown for longer than the no-progress window: a restarted client
        # must get a fresh watchdog rather than inherit the pre-hold idle time.
        hold_until = time.monotonic() + 1.3
        self.verdicts = [OK, LOST, lambda: LOST if time.monotonic() < hold_until else OK]
        self.assertEqual(self.run_with([first, second]), '')
        self.assertEqual(self.popen.call_count, 2)
        self.assertEqual(first.returncode, -15)
        self.assertIn(mock.call(first.pid, signal.SIGSTOP), self.kill.call_args_list)
        self.assertIn(mock.call(first.pid, signal.SIGTERM), self.killpg.call_args_list)
        self.assertNotIn(mock.call(first.pid, signal.SIGCONT), self.kill.call_args_list)
        self.assertIn(mock.call(second.pid, signal.SIGCONT), self.kill.call_args_list)

    def test_a_flapping_uplink_cannot_restart_a_command_past_the_first_loss_grace(self):
        self.cfg['uplink_unknown_grace_seconds'] = 1
        started = time.monotonic()
        self.verdicts = [lambda: OK if len(self.probes) % 2 else LOST]  # permitted, then lost, forever
        with self.assertRaises(backup.UplinkUnknown):
            self.run_with([Child() for _ in range(100)])
        self.assertLess(time.monotonic() - started, 5)
        self.assertGreater(self.popen.call_count, 1)

    def test_grace_zero_kills_and_defers_without_another_probe(self):
        self.cfg['uplink_unknown_grace_seconds'] = 0
        self.verdicts = [OK, LOST]
        with mock.patch.object(backup.time, 'sleep', wraps=time.sleep) as sleep:
            with self.assertRaisesRegex(backup.UplinkUnknown, UNKNOWN):
                self.run_with([Child()])
        self.assertEqual(len(self.probes), 2)
        self.assertNotIn(mock.call(icloud_uplink_hold.RECHECK_SECONDS), sleep.call_args_list)
        self.assertTrue(self.signals(signal.SIGTERM))
        self.popen.assert_called_once()

    def test_short_grace_mid_command_expires_with_one_client_started(self):
        self.cfg['uplink_unknown_grace_seconds'] = 0.4
        self.verdicts = [OK, LOST]
        started = time.monotonic()
        with self.assertRaises(backup.UplinkUnknown):
            self.run_with([Child()])
        self.popen.assert_called_once()
        self.assertLess(time.monotonic() - started, 3)

    def test_verified_metered_verdict_mid_command_defers_without_a_hold(self):
        self.verdicts = [OK, METERED]
        with self.assertRaises(backup.Deferred) as caught:
            self.run_with([Child()])
        self.assertNotIsInstance(caught.exception, backup.UplinkUnknown)
        self.assertEqual((len(self.probes), self.reports), (2, []))

    def test_restarted_delete_accepts_already_gone_but_a_first_attempt_does_not(self):
        self.verdicts = [OK, LOST, OK]
        self.assertEqual(self.run_with([Child(), Child(4)], 'deletefile'), '')
        self.verdicts = [OK]
        with self.assertRaisesRegex(RuntimeError, r'rclone deletefile failed \(exit 4\)'):
            self.run_with([Child(4)], 'deletefile')

    def test_failures_after_a_restart_keep_their_original_type(self):
        self.verdicts = [OK, LOST, OK]
        with self.assertRaises(RuntimeError) as caught:
            self.run_with([Child(), Child(1)], 'copy')
        self.assertNotIsInstance(caught.exception, backup.Deferred)
        self.assertEqual(str(caught.exception), 'rclone copy failed (exit 1)')

    def test_commands_with_partial_output_state_are_not_restarted(self):
        self.verdicts = [OK, LOST, OK]
        with self.assertRaisesRegex(backup.UplinkUnknown, UNKNOWN):
            self.run_with([Child(), Child(0)], 'hashsum')
        self.popen.assert_called_once()

    def test_cloud_readiness_holds_with_work_checks(self):
        (self.root / 'authenticated.json').write_text('{}')
        self.verdicts = [LOST, OK]
        with mock.patch.object(backup, 'STATE_DIR', self.root), \
                mock.patch.object(backup, 'credentials_ready', return_value=True):
            backup.require_cloud_ready(self.cfg)
            self.flag.touch(); self.verdicts = [LOST, OK]
            with self.assertRaisesRegex(backup.Deferred, 'ignition is on'):
                backup.require_cloud_ready(self.cfg)

    def test_manual_paths_never_hold_or_check_parking(self):
        self.flag.touch(); self.policy = '0 0 0'
        self.verdicts = [OK]
        self.enterContext(mock.patch.object(backup, 'credentials_ready', return_value=True))
        result = backup.preflight(self.cfg)
        self.assertTrue(result['uplink_allowed'])
        self.verdicts = [LOST]
        with mock.patch.object(backup.time, 'sleep'):
            result = backup.preflight(self.cfg)
        self.assertEqual((result['uplink_allowed'], result['reason']), (False, UNKNOWN))
        self.assertEqual(self.reports, [])
        attempts = []
        def attempt(args, cfg, network, interactive, parked, *rest):
            attempts.append((parked, rest[-2]))
            return next(self.root.glob('canary-*.txt')).read_text() if args[1] == 'cat' else ''
        with mock.patch.object(backup, 'STATE_DIR', self.root), \
                mock.patch.object(backup, 'run_attempt', side_effect=attempt), \
                mock.patch.object(backup.subprocess, 'run'):
            backup.verify_login(self.cfg)
        self.assertEqual(attempts, [(False, None)] * 3)


class HeartbeatStateTests(unittest.TestCase):
    def test_reporter_writes_and_clears_the_hold_without_touching_progress(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(backup, 'STATE_DIR', Path(tmp)):
            state = {'progress_updated_at': 5}
            report = icloud_uplink_hold.state_reporter(
                state, lambda: backup.atomic_json(backup.STATE_DIR / 'state.json', state))
            report({'reason': UNKNOWN, 'since': 1, 'checked_at': 2, 'fresh_until': 3})
            saved = json.loads((Path(tmp) / 'state.json').read_text())
            self.assertEqual((saved['uplink_hold']['reason'], saved['progress_updated_at']), (UNKNOWN, 5))
            report(None)
            self.assertNotIn('uplink_hold', json.loads((Path(tmp) / 'state.json').read_text()))


class ConfigurationTests(unittest.TestCase):
    def test_both_workers_validate_the_grace_after_every_overlay(self):
        base = json.loads((REPO / 'pi/configs/icloud-backup.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            for value, valid in ((0, True), (300, True), (90, True), (301, False), (-1, False),
                                 (90.0, False), (True, False), ('90', False)):
                path.write_text(json.dumps({**base, 'uplink_unknown_grace_seconds': value}))
                if valid:
                    self.assertEqual(backup.load_config(path, apply_preferences=False)['uplink_unknown_grace_seconds'], value)
                else:
                    with self.assertRaises(ValueError):
                        backup.load_config(path, apply_preferences=False)
            path.write_text(json.dumps(base))
            self.assertEqual(backup.load_config(path, apply_preferences=False)['uplink_unknown_grace_seconds'], 90)


SIGTERM_SCRIPT = textwrap.dedent('''
    import json, os, signal, sys, time
    from unittest import mock
    import icloud_backup as backup
    import icloud_uplink_hold
    from pi.tests.backup.metered_fixtures import decide
    mode, marker = sys.argv[1], sys.argv[2]
    ok = {"checked_at": 1000, "local_route": {"gateway": "192.168.6.1", "dev": "eth0", "prefsrc": "192.168.6.103"},
          "router": {"reachable": True, "default_policy": "balanced", "route_members": [{"name": "wan", "percent": 100}]},
          "rule_names": ["default_rule_v4"], "ipv6_defaults": [],
          "ubnt": {"reachable": True, "connected": True, "ssid_verified": "FriendsWifi"}, "wireless_uplinks": {}}
    lost = json.loads(json.dumps(ok)); lost["ubnt"]["ssid_verified"] = None
    probes = []
    def capture(args, timeout=None):
        probes.append(1)
        if mode == "hold":
            return json.dumps(lost)
        if len(probes) == 1:
            return json.dumps(ok)
        open(marker, "w").write(str(backup.CHILD.pid))
        time.sleep(30)
    def log(text, flush=True):
        open(marker, "w").write("holding")
    backup.capture = capture
    backup.decide = lambda value, consumer_id: decide(value, now=1000, consumer_id=consumer_id)
    backup.check_work_allowed = lambda cfg: None
    backup.admission.client_fds = lambda **kwargs: ()
    icloud_uplink_hold.print = log
    icloud_uplink_hold.RECHECK_SECONDS = 0.05
    signal.signal(signal.SIGTERM, backup.abort)
    cfg = {"rclone_config": "/dev/null", "bandwidth_limit": "1M", "guard_interval_seconds": 0.01,
           "no_progress_timeout_seconds": 60, "uplink_unknown_grace_seconds": 60}
    try:
        if mode == "hold":
            backup.guard(cfg, backup.worker_hold(cfg))
        else:
            backup.run([sys.argv[3], "lsjson", "remote:"], cfg, network=True)
    except backup.Deferred as exc:
        print("Deferred: " + str(exc), flush=True)
''')


class RealProcessSigtermTests(unittest.TestCase):
    def start(self, tmp, mode, *extra):
        marker = Path(tmp) / 'marker'
        process = subprocess.Popen([sys.executable, '-c', SIGTERM_SCRIPT, mode, str(marker), *extra],
                                   stdout=subprocess.PIPE, text=True, cwd=REPO, env=os.environ.copy())
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.05)
        self.assertTrue(marker.exists(), 'worker never reached its hold')
        return process

    def stop(self, process):
        process.send_signal(signal.SIGTERM)
        started = time.monotonic()
        output, _ = process.communicate(timeout=10)
        self.assertLess(time.monotonic() - started, 3)
        self.assertIn('Deferred: stopped for disk lifecycle or service shutdown', output)

    def test_sigterm_ends_a_hold_with_no_client_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.stop(self.start(tmp, 'hold'))

    def test_sigterm_during_a_mid_command_probe_reaps_the_stopped_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Path(tmp) / 'client'
            client.write_text('#!/bin/sh\nexec sleep 60\n')
            client.chmod(0o700)
            process = self.start(tmp, 'client', str(client))
            # The script records the stopped client's pid when its probe begins.
            deadline = time.monotonic() + 5
            while not (Path(tmp) / 'marker').read_text().isdigit() and time.monotonic() < deadline:
                time.sleep(0.05)
            pid = int((Path(tmp) / 'marker').read_text())
            self.stop(process)
            for _ in range(40):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail('stopped client was not reaped')
