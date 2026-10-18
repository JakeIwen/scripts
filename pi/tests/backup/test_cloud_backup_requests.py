"""Back up now bypasses only cadence and coalesces until one success."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import cloud_backup_requests as requests
import cloud_backup_control as controls
import icloud_backup as cloud
import time_machine_icloud as tm
from time_machine_store import atomic_json


class RequestTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = {'last_success_at':1000,'last_generation':'old','pending':None}
        atomic_json(self.root/'state.json',self.state)
        self.command = mock.Mock(return_value=subprocess.CompletedProcess([],0,'',''))

    def test_request_is_durable_coalesced_and_does_not_rewrite_progress(self):
        before = (self.root/'state.json').read_bytes()
        controls.control('backup-now',directory=self.root,service='fixture',now=1100,command=self.command)
        first = (self.root/'backup-now.json').read_bytes()
        controls.control('backup-now',directory=self.root,service='fixture',now=1200,command=self.command)
        self.assertEqual((self.root/'backup-now.json').read_bytes(),first)
        self.assertEqual((self.root/'state.json').read_bytes(),before)
        self.assertTrue(requests.due({'interval_days':7},self.state,self.root,1200))
        self.assertTrue(requests.pending(self.root))

    def test_one_success_consumes_request_without_creating_a_second_copy(self):
        requests.request(self.root,1100)
        completed = {**self.state,'last_success_at':1200,'last_generation':'new'}
        atomic_json(self.root/'state.json',completed)
        self.assertFalse(requests.pending(self.root))
        self.assertFalse(requests.due({'interval_days':7},completed,self.root,1300))
        requests.request(self.root,1400)
        self.assertTrue(requests.pending(self.root))

    def test_resume_alone_does_not_force_and_backup_now_removes_only_own_pause(self):
        controls.control('resume',directory=self.root,service='fixture',now=1100,command=self.command)
        self.assertFalse(requests.due({'interval_days':7},self.state,self.root,1100))
        atomic_json(self.root/'pause.json',{'paused_until':'indefinite'})
        controls.control('backup-now',directory=self.root,service='fixture',now=1200,command=self.command)
        self.assertFalse(controls.is_paused(self.root,1200))

    def test_existing_pending_generation_stays_selected(self):
        self.state.update(pending='unfinished',progress={'verified_files':42})
        atomic_json(self.root/'state.json',self.state)
        requests.request(self.root,1100)
        self.assertEqual(json.loads((self.root/'state.json').read_text()),self.state)

    def test_both_workers_still_enforce_login_after_requested_early_copy(self):
        requests.request(self.root,1100)
        cfg={'interval_days':7}
        with mock.patch.object(cloud,'STATE_DIR',self.root), mock.patch.object(tm,'STATE',self.root), \
             mock.patch.object(cloud,'credentials_ready',return_value=False):
            for weekly in (cloud.weekly,tm.weekly):
                with self.assertRaises(cloud.Deferred): weekly(cfg,self.state)

    def test_failed_start_keeps_request_for_a_later_retry(self):
        self.command.side_effect=subprocess.CalledProcessError(1,['systemctl'])
        with self.assertRaises(subprocess.CalledProcessError):
            controls.control('backup-now',directory=self.root,service='fixture',now=1200,command=self.command)
        self.assertTrue(requests.pending(self.root))
