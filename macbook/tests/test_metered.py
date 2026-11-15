from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from shared.metered.records import Actual, Host, HostState
from shared.metered.store import RevisionConflict
from shared.python import tool_settings
from macbook.metered.cli import main
from macbook.metered.config import SETTINGS_TOOL
from macbook.metered.launchd import configuration
from macbook.metered.network import collect
from macbook.tests.metered_support import MacFixture

ROOT = Path(__file__).resolve().parents[2]


class MacMeteredTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = MacFixture(self.temp.name)
        self.env = patch.dict(os.environ, {'JACOBR_CONFIG_DIR': str(self.fixture.paths.policy.parent)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.controller = self.fixture.controller

    def cli(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(list(args), controller=self.controller)
        return code, output.getvalue()

    def enable(self):
        tool_settings.save(SETTINGS_TOOL, {'enabled': True})

    def test_tick_is_wire_compatible_and_disabled_by_default(self):
        state = self.controller.tick()
        self.assertEqual(state, HostState.from_dict(json.loads(self.controller.state.path.read_text())))
        self.assertTrue(state.effective.metered)
        self.assertFalse(state.consumers['mac.low_data_mode'].permitted)
        self.assertEqual(state.consumers['mac.low_data_mode'].actual, Actual.NA)
        self.assertEqual(self.fixture.sudo_calls(), [])
        self.assertEqual(self.fixture.reports[0].host, Host.MAC)
        row = next(row for row in self.controller.status()['registry'] if row['id'] == 'mac.low_data_mode')
        self.assertEqual(row['how'], 'unprotected')
        self.assertEqual(self.fixture.ssh_calls()[0][-3:], ['vanpi', '/home/pi/scripts/vanpi-metered', 'sync'])
        self.assertIn('BatchMode=yes', self.fixture.ssh_calls()[0])

    def test_vpn_default_route_still_finds_the_wifi_gateway(self):
        self.fixture.vpn = True
        network = collect(self.fixture.run, self.fixture.wifi, lambda: self.fixture.now)
        self.assertTrue(network.connected)
        self.assertEqual(network.gateway, self.fixture.remote.load().home_network.gateway)
        self.assertNotEqual(self.controller.tick().verdict.reason, 'Mac network is disconnected')

    def test_offline_add_restricts_without_inventing_revision(self):
        self.fixture.lan, self.fixture.ssid = False, 'Cafe'
        self.controller.tick()
        old = self.controller.cache.config.load()
        self.assertFalse(self.controller.current().effective.metered)
        self.fixture.online = False
        code, value = self.cli('ssid', 'add', 'Cafe')
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(value)['pending_sync'])
        self.assertEqual(self.cli('allows', 'mac.codex_update')[0], 1)
        state = self.controller.tick()
        self.assertTrue(state.verdict.metered)
        self.assertEqual(self.controller.cache.overlay().revision, old.revision)
        self.assertNotIn('Cafe', self.controller.cache.config.load().metered_ssids)
        self.assertIn('Cafe', self.controller.cache.overlay().metered_ssids)

    def test_lost_response_replays_same_id_and_does_not_resurrect_removed_ssid(self):
        self.controller.add_ssid('Cafe')
        op = self.controller.cache.pending.load().ops[0]
        self.fixture.lose_reply = True
        self.controller.tick(force_sync=True)
        self.assertEqual(self.controller.cache.pending.load().ops[0], op)
        remote = self.fixture.remote.load()
        self.fixture.remote.edit_ssid('remove', 'Cafe', remote.revision)
        revision = self.fixture.remote.load().revision
        self.fixture.lose_reply = False
        self.controller.tick(force_sync=True)
        self.assertEqual(self.controller.cache.pending.load().ops, [])
        self.assertNotIn('Cafe', self.controller.cache.overlay().metered_ssids)
        self.assertEqual(self.controller.cache.config.load().revision, revision)

    def test_remove_requires_revision_and_live_roundtrip(self):
        self.controller.tick()
        self.assertEqual(self.cli('ssid', 'remove', 'denlink')[0], 2)
        self.fixture.online = False
        self.assertEqual(self.cli('ssid', 'remove', 'denlink', '--revision', '0')[0], 2)
        self.fixture.online = True
        self.assertEqual(self.cli('ssid', 'remove', 'denlink', '--revision', '4')[0], 75)
        self.assertEqual(self.cli('ssid', 'remove', 'denlink', '--revision', '0')[0], 0)
        self.assertNotIn('denlink', self.controller.cache.overlay().metered_ssids)

    def test_add_ids_are_fresh_and_exact_ssids_are_preserved(self):
        self.controller.add_ssid(' Cafe ')
        self.controller.add_ssid(' Cafe ')
        pending = self.controller.cache.pending.load().ops
        self.assertNotEqual(pending[0].op_id, pending[1].op_id)
        self.assertEqual([op.ssid for op in pending], [' Cafe ', ' Cafe '])
        self.assertEqual(self.cli('ssid', 'add', '')[0], 2)

    def test_remote_attempts_throttled_including_failures(self):
        self.fixture.lan, self.fixture.ssid, self.fixture.online = False, 'Cafe', False
        self.controller.tick()
        self.fixture.advance(60)
        self.controller.tick()
        self.assertEqual(len(self.fixture.ssh_calls()), 1)
        self.fixture.advance(840)
        self.controller.tick()
        self.assertEqual(len(self.fixture.ssh_calls()), 2)
        self.controller.tick(force_sync=True)
        self.assertEqual(len(self.fixture.ssh_calls()), 3)

    def test_lan_syncs_every_run(self):
        self.controller.tick()
        self.controller.tick()
        self.assertEqual(len(self.fixture.ssh_calls()), 2)

    def test_failed_syncs_do_not_extend_home_evidence_deadline(self):
        self.fixture.pi_metered = False
        evidence_at = self.fixture.now
        state = self.controller.tick()
        self.assertFalse(state.verdict.metered)
        self.assertEqual(state.valid_until, evidence_at + 75)
        self.fixture.online = False
        for age in (31, 60, 89, 90, 91):
            with self.subTest(age=age):
                self.fixture.now = evidence_at + age
                state = self.controller.tick()
                if age <= 90:
                    self.assertFalse(state.verdict.metered)
                    self.assertEqual(state.valid_until, evidence_at + 90)
                    self.assertEqual(self.cli('allows', 'mac.codex_update')[0], int(age == 90))
                else:
                    self.assertIsNone(state.verdict.metered)
                    self.assertFalse(state.consumers['mac.codex_update'].permitted)
        self.assertIsNotNone(self.controller.status()['sync']['error'])

    def test_home_evidence_age_boundaries(self):
        self.fixture.pi_metered = False
        for age in (29, 30, 31, 89, 90, 91):
            with self.subTest(age=age):
                self.fixture.pi_evidence = self.fixture.now - age
                state = self.controller.tick()
                if age <= 90:
                    self.assertFalse(state.verdict.metered)
                    self.assertEqual(state.valid_until,
                                     min(self.fixture.now + 75, self.fixture.pi_evidence + 90))
                    self.assertEqual('within grace' in state.verdict.reason, age > 30)
                else:
                    self.assertIsNone(state.verdict.metered)
                    self.assertEqual(state.verdict.reason, 'Pi uplink state is missing or stale')

    def test_expired_pi_leases_are_accepted_only_within_evidence_grace(self):
        self.fixture.pi_metered = False
        self.controller.tick()
        pi = self.controller.cache.pi.load()
        self.fixture.online = False
        cases = [(self.fixture.now, self.fixture.now - 1, 42, True),
                 (self.fixture.now - 76, self.fixture.now + 30, 42, True),
                 (self.fixture.now - 76, self.fixture.now - 1, 91, False)]
        for evaluated, deadline, age, accepted in cases:
            with self.subTest(evaluated=evaluated, deadline=deadline, age=age):
                self.controller.cache.pi.save_locked(replace(
                    pi, evaluated_at=evaluated, valid_until=deadline,
                    verdict=replace(pi.verdict, evidence_at=self.fixture.now - age)))
                state = self.controller.tick()
                self.assertIs(state.verdict.metered, False if accepted else None)
                if accepted:
                    self.assertIn('within grace', state.verdict.reason)

    def test_home_grace_does_not_override_invalid_pi_records(self):
        self.fixture.pi_metered = False
        self.controller.tick()
        pi = self.controller.cache.pi.load()
        pi = replace(pi, verdict=replace(pi.verdict, evidence_at=self.fixture.now - 42))
        self.fixture.online = False
        invalid = [replace(pi, host=Host.MAC),
                   replace(pi, evaluated_at=self.fixture.now + 1),
                   replace(pi, verdict=replace(pi.verdict, evidence_at=self.fixture.now + 1)),
                   replace(pi, verdict=replace(pi.verdict, evidence_at=None)),
                   replace(pi, valid_until=None),
                   replace(pi, config_revision=pi.config_revision + 1),
                   replace(pi, verdict=replace(pi.verdict, metered=None))]
        for record in invalid:
            with self.subTest(record=record):
                self.controller.cache.pi.save_locked(record)
                state = self.controller.tick()
                self.assertIsNone(state.verdict.metered)
                self.assertFalse(state.consumers['mac.codex_update'].permitted)
        self.controller.cache.pi.save_locked(pi)
        self.controller.add_ssid('New metered network')
        self.assertEqual(self.controller.tick().verdict.reason,
                         'Pi has not evaluated pending metered SSIDs')

    def test_fresh_pi_response_replaces_graced_unmetered_state(self):
        for replacement in (None, True):
            with self.subTest(replacement=replacement):
                self.fixture.online, self.fixture.pi_metered = True, False
                self.fixture.pi_evidence = self.fixture.now - 42
                self.assertFalse(self.controller.tick().verdict.metered)
                self.fixture.pi_evidence, self.fixture.pi_metered = None, replacement
                state = self.controller.tick()
                self.assertIs(state.verdict.metered, replacement)
                self.assertNotIn('within grace', state.verdict.reason)
                self.assertFalse(state.consumers['mac.codex_update'].permitted)

    def test_direct_metered_ssid_is_enforced_on_next_tick(self):
        self.enable()
        self.fixture.pi_metered = False
        self.fixture.pi_evidence = self.fixture.now - 42
        self.assertFalse(self.controller.tick().effective.metered)
        self.fixture.online, self.fixture.lan, self.fixture.ssid = False, False, 'denlink'
        state = self.controller.tick()
        self.assertTrue(state.verdict.metered)
        self.assertEqual(state.verdict.reason, 'Mac joined a metered SSID')
        self.assertTrue(self.fixture.flag)

    def test_local_policy_expiry_bounds_graced_home_admission(self):
        self.fixture.pi_metered = False
        self.fixture.pi_evidence = self.fixture.now - 42
        self.controller.tick()
        self.assertEqual(self.cli('mode', 'metered', '--for', '5s')[0], 0)
        self.assertEqual(self.controller.tick().valid_until, self.fixture.now + 5)
        self.cli('mode', 'auto')
        self.assertEqual(self.cli('approve', 'mac.codex_update', '3s')[0], 0)
        self.assertEqual(self.controller.tick().valid_until, self.fixture.now + 3)

    def test_sync_failure_and_slow_tick_diagnostics_preserve_wire_output(self):
        self.fixture.online = False
        errors = io.StringIO()
        with redirect_stderr(errors), patch('macbook.metered.controller.time.monotonic',
                                             side_effect=[0, 1, 12, 13]):
            code, output = self.cli('tick')
        self.assertEqual(code, 0)
        state = HostState.from_dict(json.loads(output))
        self.assertEqual(state, HostState.from_dict(json.loads(self.controller.state.path.read_text())))
        failed, slow = [json.loads(line) for line in errors.getvalue().splitlines()]
        self.assertEqual(failed, {'event': 'metered_sync_failed', 'at': self.fixture.now,
                                 'attempted_at': self.fixture.now, 'sync_seconds': 11,
                                 'error': self.controller.cache.status.load().error})
        self.assertEqual(slow, {'event': 'metered_tick_slow', 'at': self.fixture.now,
                               'tick_seconds': 13})

    def test_fast_successful_tick_has_no_stderr_diagnostics(self):
        for elapsed in (1, 10):
            errors = io.StringIO()
            with redirect_stderr(errors), patch('macbook.metered.controller.time.monotonic',
                                                 side_effect=[0, 0, 0, elapsed]):
                self.controller.tick()
            self.assertEqual(errors.getvalue(), '')

    def test_pi_mode_is_not_a_mac_override(self):
        self.fixture.pi_metered = True
        self.controller.tick()
        pi = self.controller.cache.pi.load()
        self.controller.cache.pi.save_locked(replace(pi, effective=replace(pi.effective, metered=False)))
        self.fixture.online = False
        self.assertTrue(self.controller.tick().effective.metered)

    def test_missing_config_and_state_fail_closed_without_probes(self):
        self.assertEqual(self.cli('is-metered')[0], 0)
        self.assertEqual(self.cli('allows', 'mac.codex_update')[0], 1)
        self.assertEqual(self.cli('status', '--json')[0], 0)
        self.assertEqual(self.fixture.calls, [])
        self.assertFalse(self.fixture.paths.directory.exists())

    def test_read_only_admission_expiry_and_wrong_host_exit_codes(self):
        self.fixture.lan, self.fixture.ssid = False, 'Cafe'
        self.controller.tick()
        calls = len(self.fixture.calls)
        self.assertEqual(self.cli('is-metered', '-v')[0], 1)
        self.assertEqual(self.cli('allows', 'mac.codex_update')[0], 0)
        self.assertEqual(self.cli('allows', 'pi.codex_update')[0], 1)
        self.assertEqual(len(self.fixture.calls), calls)
        self.fixture.advance(76)
        self.assertEqual(self.cli('is-metered')[0], 0)
        self.assertEqual(self.cli('allows', 'mac.codex_update')[0], 1)
        self.assertEqual(len(self.fixture.calls), calls)

    def test_approval_expiry_and_revision_invalidate_cached_permission(self):
        self.controller.tick()
        self.assertEqual(self.cli('approve', 'mac.codex_update', '1h')[0], 0)
        self.assertEqual(self.cli('allows', 'mac.codex_update')[0], 1)
        self.controller.tick()
        self.assertEqual(self.cli('allows', 'mac.codex_update')[0], 0)
        self.fixture.advance(3600)
        self.controller.tick()
        self.assertEqual(self.cli('allows', 'mac.codex_update')[0], 1)
        self.assertEqual(self.cli('mode', 'auto', '--revision', '0')[0], 75)
        self.assertEqual(self.cli('mode', 'unmetered')[0], 2)
        self.assertEqual(self.cli('approve', 'pi.torrents', '1h')[0], 2)

    def test_episode_approval_survives_invalidation_but_not_unmetered_transition(self):
        self.controller.tick()
        self.assertEqual(self.cli('approve', 'mac.codex_update', 'episode')[0], 0)
        self.controller.tick()
        self.assertEqual(self.cli('allows', 'mac.codex_update')[0], 0)
        self.fixture.pi_metered = False
        self.controller.tick()
        self.fixture.pi_metered = True
        self.controller.tick()
        self.assertEqual(self.cli('allows', 'mac.codex_update')[0], 1)

    def test_never_clear_native_or_wrong_network_flags(self):
        self.enable()
        self.fixture.flag = True
        self.controller.tick()
        self.cli('approve', 'mac.low_data_mode', '1h')
        self.controller.tick(restore=True)
        self.assertTrue(self.fixture.flag)
        self.assertEqual(self.fixture.sudo_calls(), [])
        self.assertEqual(self.controller.state.load().consumers['mac.low_data_mode'].actual, Actual.MANUAL)

    def test_reassert_owned_flag_then_clear_when_permitted(self):
        self.enable()
        self.controller.tick()
        self.assertTrue(self.fixture.flag)
        self.fixture.flag = False
        self.fixture.advance(60)
        self.controller.tick()
        self.assertTrue(self.fixture.flag)
        self.assertEqual(len(self.fixture.sudo_calls()), 2)
        self.cli('approve', 'mac.low_data_mode', '1h')
        self.controller.tick()
        self.assertFalse(self.fixture.flag)
        self.assertEqual(self.fixture.sudo_calls()[-1][-1], '-constrained')
        self.assertEqual(self.controller.state.load().consumers['mac.low_data_mode'].actual, Actual.RUNNING)

    def test_ownership_survives_roaming_and_sleep(self):
        for transition in ('bssid', 'sleep'):
            with self.subTest(transition=transition):
                self.fixture.flag = False
                self.enable()
                self.controller.tick()
                self.assertTrue(self.fixture.flag)
                self.cli('approve', 'mac.low_data_mode', '1h')
                if transition == 'bssid':
                    self.fixture.bssid = 'aa:bb:cc:dd:ee:ff'
                else:
                    self.fixture.advance(120)
                self.controller.tick()
                self.assertFalse(self.fixture.flag)
                self.cli('revoke', 'mac.low_data_mode')

    def test_sudo_failure_is_unknown_not_claimed_held(self):
        self.enable()
        self.fixture.fail_sudo = True
        self.assertEqual(self.controller.tick().consumers['mac.low_data_mode'].actual, Actual.UNKNOWN)

    def test_disabling_does_not_touch_flags(self):
        self.enable()
        self.controller.tick()
        count = len(self.fixture.sudo_calls())
        self.cli('low-data-mode', 'off')
        self.controller.tick()
        self.assertTrue(self.fixture.flag)
        self.assertEqual(len(self.fixture.sudo_calls()), count)
        self.assertEqual(self.cli('low-data-mode', 'on')[0], 2)

    def test_bad_state_and_future_state_fail_closed(self):
        self.controller.tick()
        state = self.controller.state.load()
        self.controller.state.save_locked(replace(state, evaluated_at=self.fixture.now + 10))
        self.assertEqual(self.cli('allows', 'mac.codex_update')[0], 1)
        self.controller.state.path.write_text('{bad')
        self.assertEqual(self.cli('allows', 'mac.codex_update')[0], 1)
        self.assertEqual(self.cli('is-metered')[0], 0)

    def test_stable_launcher_from_unrelated_directory_uses_system_python(self):
        launcher = ROOT / 'macbook/metered/mac-metered'
        env = dict(os.environ, HOME=self.temp.name)
        result = subprocess.run([str(launcher), 'status', '--json'], cwd=self.temp.name,
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['fresh'])
        for args, expected in [(['is-metered'], 0), (['allows', 'mac.codex_update'], 1)]:
            self.assertEqual(subprocess.run([str(launcher), *args], cwd=self.temp.name,
                                          env=env, capture_output=True).returncode, expected)

    def test_launchagent_paths_and_interval(self):
        plist = configuration(ROOT, Path(self.temp.name))
        self.assertEqual(plist['ProgramArguments'], ['/usr/bin/python3', '-B', '-m', 'macbook.metered', 'tick'])
        self.assertEqual(plist['StartInterval'], 15)
        self.assertTrue(plist['RunAtLoad'])
        self.assertNotIn('KeepAlive', plist)
        self.assertEqual(plist['WorkingDirectory'], str(ROOT))
        self.assertTrue(Path(plist['StandardOutPath']).is_absolute())


if __name__ == '__main__':
    unittest.main()
