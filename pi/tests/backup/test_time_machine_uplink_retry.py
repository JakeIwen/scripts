"""The Time Machine worker's capped quick retry after an expired uplink hold, through its real handler."""
from contextlib import nullcontext
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

import icloud_backup as cloud
import time_machine_store as store
from pi.tests.backup.test_time_machine_icloud import GEN, job


class UplinkRetryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        self.enterContext(mock.patch.object(job.priority, 'DIRECTORY', self.directory / 'priority'))
        # Never read the host's real /etc/vanpi-backup-settings.json.
        self.enterContext(mock.patch.object(job.backup_settings, 'overrides', return_value={}))

    def run_uplink_deferral(self, weekly):
        self.enterContext(mock.patch.object(job.admission, 'admitted_run', return_value=nullcontext()))
        with mock.patch.object(job, 'STATE', self.directory), mock.patch.object(job, 'private_directory'), \
                mock.patch.object(cloud, 'STATE_DIR', self.directory), \
                mock.patch.object(job, 'config', return_value={'interval_days': 7}), \
                mock.patch.object(job.signal, 'signal'), mock.patch('builtins.print'), \
                mock.patch.object(sys, 'argv', ['worker', '--run']), \
                mock.patch.object(job, 'weekly', side_effect=weekly):
            self.assertEqual(job.main(), 0)
        return store.read_json(self.directory / 'state.json')

    def test_uplink_quick_retries_are_capped_without_durable_advancement(self):
        for initial in ({'last_success_at': 100}, {'pending': GEN}):  # a first copy cannot bypass the cap
            with self.subTest(initial=initial):
                store.atomic_json(self.directory / 'state.json', initial)
                def prerequisites_then_unknown(cfg, state):
                    # Reaching the transfer resets quick_retries, never the uplink cap.
                    job.history.transfer_started(state)
                    raise cloud.UplinkUnknown('selected uplink identity is unknown')
                for attempt in range(job.history.QUICK_RETRY_LIMIT):
                    state = self.run_uplink_deferral(prerequisites_then_unknown)
                    self.assertGreater(state['retry_at'], time.time() + 295, attempt)
                    self.assertEqual(state['uplink_quick_retries'], attempt + 1)
                state = self.run_uplink_deferral(prerequisites_then_unknown)
                self.assertIsNone(state['retry_at'])
                self.assertEqual(state['phase'], 'deferred')

    def test_durable_advancement_resets_the_uplink_retry_cap(self):
        store.atomic_json(self.directory / 'state.json', {'last_success_at': 100, 'uplink_quick_retries': 3})
        def deleted_then_unknown(cfg, state):
            job.uplink_hold.note_advancement()
            raise cloud.UplinkUnknown('router is unreachable')
        state = self.run_uplink_deferral(deleted_then_unknown)
        self.assertEqual(state['uplink_quick_retries'], 1)
        self.assertGreater(state['retry_at'], time.time() + 295)
        store.atomic_json(self.directory / 'state.json', {'last_success_at': 100, 'uplink_quick_retries': 3})
        def advanced_and_finished(cfg, state):
            job.uplink_hold.note_advancement()
            return 'complete'
        self.assertEqual(self.run_uplink_deferral(advanced_and_finished)['uplink_quick_retries'], 0)

    def test_stale_hold_heartbeat_is_cleared_and_a_live_one_reaches_state(self):
        store.atomic_json(self.directory / 'state.json', {'last_success_at': 100,
                          'uplink_hold': {'reason': 'old', 'since': 1, 'checked_at': 1, 'fresh_until': 2}})
        seen = []
        def holds(cfg, state):
            seen.append(dict(state))
            job.uplink_hold.REPORTER({'reason': 'stale uplink evidence', 'since': 5, 'checked_at': 6, 'fresh_until': 7})
            seen.append(store.read_json(self.directory / 'state.json')['uplink_hold']['reason'])
            job.uplink_hold.REPORTER(None)
            return 'not due'
        state = self.run_uplink_deferral(holds)
        self.assertNotIn('uplink_hold', seen[0])
        self.assertEqual(seen[1], 'stale uplink evidence')
        self.assertNotIn('uplink_hold', state)
