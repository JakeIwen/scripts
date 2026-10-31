"""Policy behavior through public records, rules and real filesystem stores."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from shared.metered.config import ConfigStore, first_run_config
from shared.metered.policy import PolicyStore
from shared.metered.records import (
    AddSSID, ApprovalKind, Effective, EffectiveSource, Host, HostPolicy, MacNetwork,
    MemberKind, ModeValue, SharedConfig, SharedOperation, SyncRequest,
    UplinkEvidence, UplinkMember,
)
from shared.metered.registry import consumers
from shared.metered.rules import dendelion_verdict, effective, mac_verdict, permits
from shared.metered.store import RevisionConflict


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.config = first_run_config()
        self.safe = UplinkMember('clientwan', 100, True, MemberKind.STATION, 'phone', 'phone')

    def verdict(self, members, now=100, **kwargs):
        return dendelion_verdict(UplinkEvidence(100, 'balanced', members, **kwargs), self.config, now)

    def test_mixed_routes_and_exact_ssid_matching(self):
        metered = UplinkMember('wan', 50, True, MemberKind.UBNT, 'denlink', 'denlink')
        self.assertTrue(self.verdict([metered, replace(self.safe, percent=50)]).metered)
        for name in ('starwan', 'lifiwan', 'clientwan'):
            self.assertTrue(self.verdict([replace(metered, name=name, percent=100,
                                                 kind=MemberKind.STATION)]).metered)
        for name in ('Denlink', 'Starlink', 'denlink '):
            self.assertFalse(self.verdict([replace(self.safe, ssid_verified=name)]).metered)
        self.assertFalse(self.verdict([replace(metered, percent=0), self.safe]).metered)

    def test_unverifiable_or_stale_routes_fail_closed(self):
        cases = [[], [replace(self.safe, percent=20)], [replace(self.safe, online=False)],
                 [replace(self.safe, ssid_verified=None)], [self.safe, self.safe]]
        for members in cases:
            verdict = self.verdict(members)
            self.assertIsNone(verdict.metered)
            self.assertTrue(effective(verdict, HostPolicy(), 100).metered)
        for now in (99, 131):
            self.assertIsNone(self.verdict([self.safe], now).metered)
        self.assertIsNone(self.verdict([self.safe], unsupported=['source routing']).metered)

    def test_mac_unknown_is_not_unmetered_and_home_needs_both_identities(self):
        home = self.config.home_network
        metered = self.verdict([replace(self.safe, ssid_verified='denlink')])
        network = MacNetwork(None, home.gateway, home.gateway_mac, True)
        self.assertEqual(mac_verdict(network, metered, self.config), metered)
        self.assertIsNone(mac_verdict(replace(network, gateway_mac=None), metered, self.config).metered)
        self.assertFalse(mac_verdict(replace(network, ssid='hotel'), metered, self.config).metered)
        self.assertTrue(mac_verdict(replace(network, ssid='denlink'), metered, self.config).metered)

    def test_episode_survives_unknown_and_ends_at_unmetered(self):
        first = effective(self.verdict([]), HostPolicy(), 100)
        again = effective(self.verdict([]), HostPolicy(), 101, first)
        self.assertEqual(first.episode, again.episode)
        ended = effective(self.verdict([self.safe]), HostPolicy(), 102, again)
        self.assertIsNone(ended.episode)
        self.assertNotEqual(first.episode, effective(self.verdict([]), HostPolicy(), 103, ended).episode)


class StoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = ConfigStore(self.root / 'config.json')
        self.policy = PolicyStore(Host.PI, self.root / 'policy.json')
        self.state = Effective(True, EffectiveSource.VERDICT, 'episode-1')

    def test_defaults_and_roundtrip_preserve_exact_identities(self):
        value = self.config.edit_ssid('add', ' Hotel ', 0)
        self.assertIn(' Hotel ', self.config.load().metered_ssids)
        self.assertEqual(SharedConfig.from_dict(json.loads(json.dumps(value.as_dict()))), value)
        self.assertEqual(self.config.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(RevisionConflict):
            self.config.edit_ssid('remove', 'denlink', 0)

    def test_lost_ack_replay_does_not_reverse_a_later_removal(self):
        operation = AddSSID('op-1', SharedOperation.ADD_SSID, 'hotel')
        value, applied = self.config.apply_ops([operation])
        self.assertEqual(applied, ['op-1'])
        self.config.edit_ssid('remove', 'hotel', value.revision)
        replay, applied = self.config.apply_ops([operation])
        self.assertNotIn('hotel', replay.metered_ssids)
        self.assertEqual(replay.revision, 2)
        with self.assertRaises(ValueError):
            self.config.apply_ops([replace(operation, ssid='different')])
        self.assertEqual(self.config.load(), replay)

    def test_receipt_batch_is_atomic(self):
        operation = AddSSID('op-1', SharedOperation.ADD_SSID, 'hotel')
        with self.assertRaises(ValueError):
            self.config.apply_ops([operation, replace(operation, ssid='different')])
        self.assertEqual(self.config.load().revision, 0)

    def test_two_revision_writers_cannot_lose_each_others_update(self):
        def edit(name):
            try:
                self.config.edit_ssid('add', name, 0)
                return 'saved'
            except RevisionConflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(edit, ['one', 'two']), ['saved', 'conflict'])
        self.assertEqual(self.config.load().revision, 1)

    def test_approval_expiry_and_host_ownership(self):
        value = self.policy.approve('pi.torrents', ApprovalKind.UNTIL, 200, self.state, 100, 0)
        self.assertTrue(permits('pi.torrents', self.state, value, 199).permitted)
        self.assertFalse(permits('pi.torrents', self.state, value, 200).permitted)
        self.assertFalse(permits('pi.torrents', self.state, value, 99).permitted)
        with self.assertRaises(ValueError):
            self.policy.approve('mac.codex_update', ApprovalKind.UNTIL, 200, self.state, 100, 1)
        self.assertEqual(self.policy.expire(self.state, 200).approvals, {})

    def test_episode_approval_never_transfers_to_next_network_episode(self):
        value = self.policy.approve('pi.torrents', ApprovalKind.EPISODE, None, self.state, 100, 0)
        self.assertTrue(permits('pi.torrents', self.state, value, 101).permitted)
        next_state = replace(self.state, episode='episode-2')
        self.assertFalse(permits('pi.torrents', next_state, value, 102).permitted)
        self.assertEqual(self.policy.expire(next_state, 102).approvals, {})

    def test_forced_unmetered_requires_bounded_expiry(self):
        for until in (None, 99, 90000):
            with self.assertRaises(ValueError):
                self.policy.set_mode(ModeValue.UNMETERED, until, 100, 0)
        value = self.policy.set_mode(ModeValue.UNMETERED, 200, 100, 0)
        self.assertEqual(value.mode.until, 200)
        self.assertEqual(self.policy.expire(self.state, 200).mode.value, ModeValue.AUTO)

    def test_invalid_records_and_symlinks_do_not_default_open(self):
        self.config.path.write_text('{"revision": true}')
        with self.assertRaises(ValueError):
            self.config.load()
        self.config.path.unlink()
        target = self.root / 'target.json'
        target.write_text(json.dumps(first_run_config().as_dict()))
        self.config.path.symlink_to(target)
        with self.assertRaises(OSError):
            self.config.load()
        with self.assertRaises(ValueError):
            SyncRequest.from_dict({'ops': [{'op_id': 'x', 'op': 'remove_ssid', 'ssid': 'denlink'}],
                                   'mac_report': None})

    def test_registry_is_host_qualified_and_schema_validated(self):
        self.assertIn('pi.time_machine_cloud', consumers())
        self.assertTrue(all(key.startswith(row.host.value + '.') for key, row in consumers().items()))


if __name__ == '__main__':
    unittest.main()
