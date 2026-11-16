"""The bounded unknown-uplink hold, driven by a fake clock."""
import unittest

import icloud_uplink_hold as hold_module
from icloud_uplink_hold import RECHECK_SECONDS, RESTART_EXITS, UplinkHold, UplinkLost, apply_grace

UNKNOWN = 'selected uplink identity is unknown'
METERED = 'held on a metered or unknown network'


class Clock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class HoldTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.reports, self.logs, self.checks, self.probes = [], [], [], []

    def hold(self, grace, verdicts, lost=None, probe_seconds=1, work_check=None, revalidate=None):
        verdicts = list(verdicts)
        def probe():
            self.probes.append(self.clock.now)
            self.clock.now += probe_seconds
            allowed, reason = verdicts.pop(0)
            return ({'ok': True} if allowed else None), allowed, reason
        def check():
            self.checks.append(self.clock.now)
            if work_check:
                work_check()
        value = UplinkHold(grace, check, self.reports.append, 60, lost=lost,
                           log=lambda text, flush: self.logs.append(text),
                           clock=self.clock, wall=self.clock, sleep=self.clock.sleep)
        return value.admit(probe, revalidate or (lambda evidence: (True, 'unmetered')))

    def test_recovers_inside_the_grace_with_a_work_check_before_each_retry(self):
        evidence, reason = self.hold(90, [(False, UNKNOWN), (False, UNKNOWN), (True, 'unmetered')])
        self.assertEqual((evidence, reason), ({'ok': True}, None))
        self.assertEqual(len(self.probes), 3)
        self.assertEqual(len(self.checks), 3)  # before each retry, and after the permitted probe
        self.assertEqual(self.clock.sleeps, [RECHECK_SECONDS, RECHECK_SECONDS])
        self.assertIsNone(self.reports[-1])
        beats = [r for r in self.reports if r]
        seen = self.probes[0] + 1  # the first unknown verdict arrived when its probe returned
        self.assertTrue(beats and all(r['reason'] == UNKNOWN and r['since'] == seen
                                      and r['fresh_until'] == seen + 90 + 60 for r in beats))
        self.assertIn('verified again', self.logs[-1])

    def test_no_probe_starts_at_or_after_the_deadline(self):
        evidence, reason = self.hold(12, [(False, UNKNOWN)] * 10, probe_seconds=4)
        self.assertEqual((evidence, reason), (None, UNKNOWN))
        self.assertTrue(all(start < 100 + 12 for start in self.probes))
        self.assertEqual(len(self.probes), 2)

    def test_a_slow_work_check_that_passes_the_deadline_stops_before_probing(self):
        def slow():
            self.clock.now += 30
        evidence, reason = self.hold(20, [(False, UNKNOWN), (True, 'unmetered')], work_check=slow)
        self.assertEqual((evidence, reason), (None, UNKNOWN))
        self.assertEqual(len(self.probes), 1)

    def test_grace_zero_is_one_probe_with_no_sleep_or_work_check(self):
        self.assertEqual(self.hold(0, [(False, UNKNOWN)]), (None, UNKNOWN))
        self.assertEqual((len(self.probes), self.clock.sleeps, self.checks), (1, [], []))

    def test_metered_verdict_refuses_at_once_even_mid_hold(self):
        self.assertEqual(self.hold(90, [(False, METERED)]), (None, METERED))
        self.assertEqual((self.clock.sleeps, self.reports), ([], []))
        self.assertEqual(self.hold(90, [(False, UNKNOWN), (False, METERED)])[1], METERED)

    def test_work_check_refusal_ends_the_hold_and_clears_the_heartbeat(self):
        def ignition():
            raise RuntimeError('ignition is on')
        with self.assertRaisesRegex(RuntimeError, 'ignition'):
            self.hold(90, [(False, UNKNOWN)], work_check=ignition)
        self.assertIsNone(self.reports[-1])

    def test_a_lost_command_continues_its_deadline_and_sleeps_before_probing(self):
        lost = UplinkLost(UNKNOWN, 60.0, 60.0)
        self.assertEqual(self.hold(50, [(True, 'unmetered')], lost=lost)[1], None)
        self.assertEqual(self.clock.sleeps, [RECHECK_SECONDS])
        self.assertEqual(self.hold(40, [(True, 'unmetered')], lost=UplinkLost(UNKNOWN, 60.0, 60.0)),
                         (None, UNKNOWN))

    def test_heartbeat_is_refreshed_on_every_iteration(self):
        self.hold(90, [(False, UNKNOWN)] * 4 + [(True, 'unmetered')])
        beats = [r for r in self.reports if r]
        self.assertEqual(len(beats), 8)
        self.assertEqual(sorted(r['checked_at'] for r in beats), [r['checked_at'] for r in beats])


class RevalidationTests(HoldTests):
    def test_proof_that_goes_stale_during_the_final_work_check_is_re_probed(self):
        def slow():
            self.clock.now += 20
        decisions = iter([(False, 'stale uplink evidence'), (True, 'unmetered')])
        seen = []
        def revalidate(evidence):
            seen.append(self.clock.now)
            return next(decisions)
        self.assertEqual(self.hold(90, [(True, 'unmetered')] * 2, work_check=slow, revalidate=revalidate),
                         ({'ok': True}, None))
        self.assertEqual(len(self.probes), 2)
        self.assertTrue(all(when > probe for when, probe in zip(seen, self.probes)))

    def test_stale_proof_with_no_grace_left_refuses(self):
        stale = lambda evidence: (False, 'stale uplink evidence')
        self.assertEqual(self.hold(0, [(True, 'unmetered')], revalidate=stale), (None, 'stale uplink evidence'))
        self.assertEqual(self.hold(90, [(True, 'unmetered')], revalidate=lambda e: (False, METERED)), (None, METERED))


class ConfigurationTests(unittest.TestCase):
    def test_default_bounds_and_types(self):
        self.assertEqual(apply_grace({})[hold_module.GRACE_KEY], 90)
        for good in (0, 90, 300):
            self.assertEqual(apply_grace({hold_module.GRACE_KEY: good})[hold_module.GRACE_KEY], good)
        for bad in (-1, 301, 90.0, True, '90', None):
            with self.assertRaises(ValueError):
                apply_grace({hold_module.GRACE_KEY: bad})


class RestartPolicyTests(unittest.TestCase):
    def test_only_idempotent_commands_restart_and_only_deletes_accept_not_found(self):
        self.assertNotIn('hashsum', RESTART_EXITS)
        self.assertEqual(RESTART_EXITS['deletefile'], {4})
        self.assertEqual(RESTART_EXITS['delete'], set())  # a batch re-lists; gone objects exit 0
        self.assertEqual(RESTART_EXITS['lsjson'], set())
