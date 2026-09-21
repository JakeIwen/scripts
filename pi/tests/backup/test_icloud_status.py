#!/usr/bin/env python3
import json
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
