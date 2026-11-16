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
from time_machine_verification import CHECKPOINT, ObjectVerifier


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

    def transfer_failing_after(self, checkpoint_new_proof):
        """weekly() runs the real transfer; its verifier checkpoints (or not), then loses the uplink."""
        store_root = self.directory / 'store'; store_root.mkdir(exist_ok=True)
        digest, old = 'a' * 64, 'b' * 64
        expected = {digest: {'sha256': digest, 'bytes': 1}, old: {'sha256': old, 'bytes': 1}}
        row = {'IsDir': False, 'Size': 1, 'ModTime': 'now', 'ID': 'x'}
        # A proof from an earlier run is already checkpointed: it is no advancement.
        ObjectVerifier(expected, store_root / CHECKPOINT).accept_hash(old, row, old)

        def verifier_then_uplink_lost(cfg, root, expected, rows, files, state):
            if checkpoint_new_proof:
                # What the verifier child does: a durable proof, then its next command loses the uplink.
                ObjectVerifier(expected, root / CHECKPOINT).accept_hash(digest, row, digest)
            raise cloud.UplinkUnknown('selected uplink identity is unknown')

        cfg = {'remote': 'icloud:VanRecovery/m4mac/time-machine', 'max_cloud_store_gib': 1000,
               'overlap_verification': True}
        def weekly(_cfg, state):
            return job.transfer(cfg, {'generation': GEN}, state)
        patches = (mock.patch.object(job, 'ROOT', store_root), mock.patch.object(job, 'ensure_remote'),
                   mock.patch.object(job.store, 'validate_manifest', return_value=expected),
                   mock.patch.object(cloud, 'remote_inventory', return_value={}),
                   mock.patch.object(job, 'upload_and_follow', side_effect=verifier_then_uplink_lost))
        for patch in patches:
            self.enterContext(patch)
        return self.run_uplink_deferral(weekly)

    def test_a_proof_checkpointed_before_the_verifier_lost_the_uplink_resets_the_cap(self):
        store.atomic_json(self.directory / 'state.json', {'last_success_at': 100, 'pending': GEN,
                                                          'uplink_quick_retries': 3})
        state = self.transfer_failing_after(checkpoint_new_proof=True)
        self.assertEqual(state['uplink_quick_retries'], 1)
        self.assertGreater(state['retry_at'], time.time() + 295)

    def test_only_earlier_proofs_leave_an_exhausted_cap_exhausted(self):
        store.atomic_json(self.directory / 'state.json', {'last_success_at': 100, 'pending': GEN,
                                                          'uplink_quick_retries': 3})
        state = self.transfer_failing_after(checkpoint_new_proof=False)
        self.assertEqual(state['uplink_quick_retries'], 3)
        self.assertIsNone(state['retry_at'])
