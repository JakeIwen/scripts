#!/usr/bin/env python3
import json
import errno
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts' / 'backup'))
import icloud_status as status
import icloud_backup as backup

GEN = 'vanpi-20260920T225254Z-ebf57e14'


class StatusTests(unittest.TestCase):
    def build(self, state=None, live=False, **kwargs):
        return status.build_status(state or {}, {}, {'interval_days': 7},
                                   {'ActiveState': 'activating' if live else 'inactive',
                                    'MainPID': '123' if live else '0'}, 1100, now=1000, **kwargs)

    def test_dead_worker_is_not_running(self):
        result = self.build({'phase': 'uploading', 'worker_pid': 123})
        self.assertFalse(result['running'])
        self.assertEqual(result['phase'], 'interrupted')
        self.assertTrue(result['attention'])

    def test_matching_worker_and_stale_heartbeat(self):
        result = self.build({'phase': 'verifying', 'worker_pid': 123,
                             'progress_updated_at': 995}, live=True)
        self.assertTrue(result['running'])
        self.assertEqual(result['phase'], 'verifying')
        self.assertFalse(result['progress_stale'])
        self.assertTrue(self.build({'phase': 'verifying', 'worker_pid': 123,
                                   'progress_updated_at': 900}, live=True)['progress_stale'])

    def test_new_worker_does_not_inherit_old_progress(self):
        result = self.build({'phase': 'uploading', 'worker_pid': 122,
                             'progress': {'command_bytes': 123}}, live=True)
        self.assertEqual(result['phase'], 'checking')
        self.assertIsNone(result['progress']['command_bytes'])

    def test_paused_keeps_numeric_progress_but_never_exports_secrets(self):
        result = self.build({'phase': 'deferred', 'pending': GEN,
                             'last_error': 'secret details: local-backup window',
                             'password': 'PRIVATE', 'progress': {'command_bytes': 500,
                                                               'current_file': 'PRIVATE'}})
        self.assertEqual(result['message'], 'Waiting for the daily local backups.')
        self.assertEqual(result['progress']['command_bytes'], 500)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('secret details', json.dumps(result))
        self.assertFalse(result['running'])
        self.assertIsNone(result['last_success_at'])

    def test_auth_attention_and_completed_freshness(self):
        self.assertEqual(self.build(auth_error=True)['phase'], 'authentication_required')
        result = self.build({'phase': 'not due', 'last_success_at': 990})
        self.assertFalse(result['attention'])
        self.assertEqual(result['next_due_at'], 990 + 7 * 86400)

    def test_queued_message_names_verified_lock_owner_and_rejects_stale_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / 'backup.lock'; lock.write_text('123\n')
            proc = root / 'proc/123'; (proc / 'fd').mkdir(parents=True)
            (proc / 'cmdline').write_bytes(b'python3\0/home/pi/scripts/backup/icloud_backup.py\0--run\0')
            self.assertNotIn('Pi iCloud', status.queued_backup_message(lock, root / 'proc'))
            (proc / 'fd/9').symlink_to(lock)
            self.assertIn('waiting for Pi iCloud backup to finish', status.queued_backup_message(lock, root / 'proc'))
            (proc / 'cmdline').write_bytes(b'python3\0/private/unrelated.py\0')
            self.assertNotIn('private', status.queued_backup_message(lock, root / 'proc'))

    def test_manual_projection_preserves_upload_history_and_clears_only_on_expiry(self):
        original = self.build({'phase': 'deferred', 'pending': GEN})
        paused = status.apply_manual_control(dict(original), 2000, now=1000)
        self.assertEqual(paused['phase'], 'paused')
        self.assertEqual(paused['next_check_at'], 2000)
        self.assertEqual(paused['generation'], GEN)
        with mock.patch.object(status, 'queued_backup_message', return_value='Waiting for local backup'):
            queued = status.apply_manual_control(dict(original), 2000, now=2000)
        self.assertIsNone(queued['manual_pause_until'])
        self.assertTrue(queued['resume_pending'])
        self.assertEqual(queued['message'], 'Waiting for local backup')

    def test_indefinite_pause_has_no_retry_deadline_or_stall_warning(self):
        result = status.apply_manual_control({**self.build(), 'stalled': True}, 'indefinite', now=10**12)
        self.assertEqual(result['phase'], 'paused')
        self.assertTrue(result['manual_pause_indefinite'])
        self.assertFalse(result['resume_pending'])
        self.assertFalse(result['stalled'])
        self.assertIsNone(result['next_check_at'])
        self.assertIsNone(result['manual_pause_until'])

    def test_stall_requires_live_fresh_transfer_idle_evidence(self):
        state = {'phase': 'uploading', 'worker_pid': 123, 'progress_updated_at': 995,
                 'progress': {'command_idle_seconds': 120}}
        self.assertTrue(self.build(state, live=True)['stalled'])
        self.assertTrue(self.build({**state, 'phase': 'verifying'}, live=True)['stalled'])
        self.assertFalse(self.build(state)['stalled'])
        for changes in ({'phase': 'preparing'}, {'progress_updated_at': 900}, {'worker_pid': 124},
                        {'progress': {'command_idle_seconds': 119}}):
            self.assertFalse(self.build({**state, **changes}, live=True)['stalled'])

    def test_speed_only_comes_from_a_fresh_matching_live_upload(self):
        state = {'phase': 'uploading', 'worker_pid': 123, 'progress_updated_at': 995,
                 'progress': {'upload_bytes_per_second': 1234.5}}
        self.assertEqual(self.build(state, live=True)['progress']['upload_bytes_per_second'], 1234.5)
        self.assertIsNone(self.build(state)['progress']['upload_bytes_per_second'])
        for changes in ({'phase': 'verifying'}, {'worker_pid': 124}, {'progress_updated_at': 900}):
            self.assertIsNone(self.build({**state, **changes}, live=True)['progress']['upload_bytes_per_second'])

    def test_parallel_verification_has_its_own_freshness_and_stall_signal(self):
        state = {'phase':'uploading','worker_pid':123,'progress_updated_at':995,
                 'progress':{'parallel_verification':True,'verification_updated_at':995,
                             'verification_bytes_per_second':2048,'verification_idle_seconds':121}}
        live = self.build(state, live=True)
        self.assertEqual(live['progress']['verification_bytes_per_second'], 2048)
        self.assertTrue(live['stalled'])
        self.assertIn('verifying completed uploads', live['message'])
        self.assertIsNone(self.build(state)['progress']['verification_bytes_per_second'])
        for changes in ({'verification_waiting':True}, {'verification_updated_at':900}):
            changed = self.build({**state,'progress':{**state['progress'],**changes}}, live=True)
            self.assertFalse(changed['stalled'])
            self.assertIsNone(changed['progress']['verification_bytes_per_second'])

    def test_exclusive_access_loss_has_a_specific_public_reason(self):
        status = self.build({'phase': 'deferred', 'last_error': 'Time Machine capture lost exclusive source access'})
        self.assertIn('exclusive Time Machine access was lost', status['message'])

    def test_storage_failure_reason_is_actionable_without_private_exception_details(self):
        code = status.classify_failure(OSError(errno.EIO, 'PRIVATE', '/secret/path'))
        value = self.build({'phase': 'deferred', 'failure_code': code, 'last_error': 'PRIVATE'})
        self.assertIn('disk I/O', value['message'])
        self.assertNotIn('PRIVATE', json.dumps(value))
        self.assertNotIn(status.classify_failure(PermissionError(errno.EACCES, 'private')), status.RETRYABLE_FAILURES)
        self.assertNotIn(status.classify_failure(OSError(errno.ENOSPC, 'private')), status.RETRYABLE_FAILURES)

    def test_staging_failure_explains_why_automatic_cleanup_stopped(self):
        value = self.build({'phase': 'error', 'last_error': 'unsafe staging object; cleanup refused'})
        self.assertIn('unrecognized or unsafe file', value['message'])

    def test_bounded_history_and_verified_evidence_survive_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            state = {'pending': GEN, 'progress': {'verified_bytes': 500}}
            for index in range(105):
                status.begin_attempt(directory, state)
                state.update(phase='deferred', last_error='local-backup window')
                if index == 0:
                    state.update(attempt_verified_at=990, phase='error')
                status.save_attempt(directory, state, finished=True)
            history = json.loads((directory / 'history.json').read_text())
            self.assertEqual(len(history['attempts']), 100)
            self.assertEqual(history['verified'], [{'generation': GEN, 'completed_at': 990}])
            self.assertEqual((directory / 'history.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual(state['progress']['verified_bytes'], 500)

    def test_stale_open_history_marked_interrupted(self):
        state = {'attempt_id': 'abc', 'attempt_started_at': 900, 'phase': 'uploading'}
        history = {'attempts': [status.attempt_row(state)]}
        result = status.build_status(state, history, {}, {'MainPID': '0'}, None, now=1000)
        self.assertEqual(result['attempts'][0]['phase'], 'interrupted')

    def test_upload_estimate_includes_prior_objects_and_clamps_retries(self):
        expected = {'a': {'bytes': 100}, 'b': {'bytes': 200}}
        inventory = {'a': {'IsDir': False, 'Size': 100, 'ModTime': 'date'},
                     'b': {'IsDir': False, 'Size': 20, 'ModTime': 'date'}}
        self.assertEqual(backup.upload_progress(expected, inventory, {'bytes': 50})['upload_estimated_bytes'], 150)
        self.assertEqual(backup.upload_progress(expected, inventory, {'bytes': 999})['upload_estimated_bytes'], 300)

    def test_phase_updates_persist_history_without_per_tick_history_writes(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(backup, 'STATE_DIR', Path(tmp)):
            state = {}
            status.begin_attempt(Path(tmp), state)
            with mock.patch.object(backup, 'save_attempt', wraps=status.save_attempt) as save:
                backup.record_progress(state, 'uploading', command_bytes=1)
                backup.record_progress(state, 'uploading', command_bytes=2)
                self.assertEqual(save.call_count, 1)
            self.assertEqual(json.loads((Path(tmp) / 'state.json').read_text())['progress']['command_bytes'], 2)


if __name__ == '__main__':
    unittest.main()
