"""Run with PYTHONPATH=pi/scripts/backup; no live services or disks are used."""
import json
import os
import unittest
from unittest import mock

import backup_priority as priority
import backup_priority_control as control
import mac_capture_control as capture
import icloud_backup as cloud
import cloud_backup_control
from time_machine_store import atomic_json, read_json

from .priority_fixtures import GEN, PriorityFixture


class PriorityTests(PriorityFixture):
    def test_cloud_first_admission_and_automatic_release_require_selected_verification(self):
        priority.set_policy('time-machine', now=1000)
        self.assertTrue(priority.allows('time-machine'))
        self.assertFalse(priority.allows('pi'))
        self.assertFalse(priority.allows('pi-cloud'))
        for kind in ('disk', 'borg', 'exfat', 'clone', 'local-borg', 'local-exfat'):
            self.assertTrue(priority.allows(kind))
        path = self.jobs[priority.PriorityMode.TIME_MACHINE].directory / 'state.json'
        for state in ({'last_success_at': 999, 'last_generation': GEN},
                      {'last_success_at': 1001, 'last_generation': 'different'}):
            atomic_json(path, state)
            self.assertEqual(priority.policy(), 'time-machine')
        atomic_json(path, {'last_success_at': 1001, 'last_generation': GEN})
        self.assertEqual(priority.policy(), 'normal')
        self.assertTrue(priority.allows('disk'))
        self.assertTrue(priority.status(now=1002)['completed'])
        priority.complete_job('time-machine', read_json(path))
        atomic_json(path, {'last_success_at': 2000, 'last_generation': 'a-later-recovery-point'})
        self.assertEqual(priority.policy(), 'normal')

    def test_pi_cloud_kind_and_legacy_alias_share_selected_policy(self):
        priority.set_policy('pi', now=1000)
        self.assertTrue(priority.allows('pi'))
        self.assertTrue(priority.allows('pi-cloud'))
        self.assertFalse(priority.allows('time-machine'))

    def test_manual_holds_survive_switching_and_cancelling_priority(self):
        for job in self.jobs.values():
            atomic_json(job.directory / 'pause.json', {'paused_until': 'indefinite'})
        priority.set_policy('time-machine', now=1000)
        priority.set_policy('pi', now=1001)
        priority.set_policy('normal', now=1002)
        for job in self.jobs.values():
            self.assertEqual(read_json(job.directory / 'pause.json'), {'paused_until': 'indefinite'})

    def test_selecting_resumes_selected_job_without_creating_a_peer_hold(self):
        command = mock.Mock()
        control.change('time-machine', command=command)
        self.assertEqual(command.call_args.args[0], ['/usr/bin/systemctl', 'start', '--no-block', 'vanpi-time-machine-icloud.service'])
        self.assertFalse((self.jobs[priority.PriorityMode.PI].directory / 'pause.json').exists())
        self.assertTrue((self.jobs[priority.PriorityMode.TIME_MACHINE].directory / 'pause.json').exists())

    def test_existing_switch_action_cannot_conflict_with_an_active_priority(self):
        priority.set_policy('time-machine', now=1000)
        command = mock.Mock(side_effect=AssertionError('A conflicting switch must not run a service command'))
        jobs = {job.service: job.directory for job in self.jobs.values()}
        with self.assertRaises(ValueError):
            cloud_backup_control.take_turn('vanpi-icloud-backup.service', jobs=jobs, command=command)
        command.assert_not_called()

    def test_priority_preserves_ignition_check_and_only_blocks_other_cloud_job(self):
        priority.set_policy('time-machine', now=1000)
        cfg = {'priority_job': priority.PriorityMode.TIME_MACHINE}
        with mock.patch.object(cloud, 'check_parked') as parked:
            cloud.check_work_allowed(cfg)
            parked.assert_called_once()
            with self.assertRaises(cloud.Deferred):
                cloud.check_work_allowed({'priority_job': priority.PriorityMode.PI})
            parked.side_effect = cloud.Deferred('ignition is on')
            with self.assertRaises(cloud.Deferred):
                cloud.check_work_allowed(cfg)
            parked.side_effect = None
            priority.set_policy('normal')
            cloud.check_work_allowed(cfg)

    def test_current_copy_or_missing_prerequisite_cannot_reserve_priority(self):
        job = self.jobs[priority.PriorityMode.PI]
        atomic_json(job.directory / 'state.json', {'last_success_at': 995})
        with self.assertRaises(ValueError):
            priority.set_policy('pi', now=1000)
        atomic_json(job.directory / 'state.json', {})
        with self.assertRaises(ValueError):
            priority.set_policy('pi', now=1000000, borg_stamp=self.root / 'missing')

    def test_unknown_modes_and_unsafe_records_fail_closed_only_for_cloud_jobs(self):
        with self.assertRaises(ValueError):
            priority.set_policy('arbitrary')
        with self.assertRaises(ValueError):
            priority.allows('arbitrary')
        priority.set_policy('time-machine', now=1000)
        record = self.directory / 'priority.json'

        def assert_local_admitted_and_cloud_refused():
            for kind in ('disk', 'borg', 'exfat', 'clone', 'local-borg', 'local-exfat'):
                self.assertTrue(priority.allows(kind))
            for kind in ('pi', 'pi-cloud', 'time-machine'):
                with self.assertRaises((OSError, ValueError)):
                    priority.allows(kind)

        record.chmod(0o000)
        assert_local_admitted_and_cloud_refused()
        record.chmod(0o600)
        record.write_text('{broken')
        assert_local_admitted_and_cloud_refused()
        record.unlink(); record.symlink_to(self.root / 'elsewhere')
        assert_local_admitted_and_cloud_refused()

    def test_initial_state_does_not_create_files_from_read_only_status(self):
        self.assertEqual(priority.policy(), 'normal')
        self.assertFalse(self.directory.exists())
        result = priority.status(now=1000)
        self.assertNotIn('local_fairness', result)
        self.assertFalse(self.directory.exists())

class CapturePermissionTests(PriorityFixture):
    def setUp(self):
        super().setUp()
        self.mac = self.jobs[priority.PriorityMode.TIME_MACHINE].directory
        atomic_json(self.mac / 'mac-coordinator.json', {'seen_at': 1000, 'stop_for_capture': True})
        atomic_json(self.mac / 'state.json', {'pending': GEN, 'progress': {'capture_waiting': True}})
        self.live = {'requested': True, 'generation': GEN, 'capture_id': 'a' * 32, 'expires_at': 1500}

    def test_requires_capable_fresh_mac_and_refuses_already_frozen_capture(self):
        with self.assertRaises(ValueError):
            capture.request_stop(self.mac, now=1400)
        atomic_json(self.mac / 'state.json', {'pending': GEN, 'progress': {'capture_complete': True}})
        with self.assertRaises(ValueError):
            capture.request_stop(self.mac, now=1000)

    def test_only_matching_live_nonce_can_claim_once(self):
        row = capture.request_stop(self.mac, now=1000)
        self.assertIsNotNone(capture.authorization(self.live, self.mac, now=1001))
        for gen, nonce, request_id in ((GEN, 'b' * 32, row['id']), ('wrong', 'a' * 32, row['id']), (GEN, 'a' * 32, 'b' * 32)):
            self.assertFalse(capture.claim_stop(gen, nonce, request_id, self.mac, lambda: self.live, now=1001)['authorized'])
        grant = capture.claim_stop(GEN, 'a' * 32, row['id'], self.mac, lambda: self.live, now=1001)
        self.assertTrue(grant['authorized']); self.assertEqual(grant['expires_at'], 1031)
        self.assertFalse(capture.claim_stop(GEN, 'a' * 32, row['id'], self.mac, lambda: self.live, now=1002)['authorized'])

    def test_cancel_expiry_or_capture_start_revokes_pending_permission(self):
        capture.request_stop(self.mac, now=1000)
        self.assertIsNone(capture.authorization(self.live, self.mac, now=2801))
        atomic_json(self.mac / 'state.json', {'pending': GEN, 'progress': {'capture_waiting': False}})
        self.assertIsNone(capture.authorization(self.live, self.mac, now=1001))
        capture.cancel_stop(self.mac)
        self.assertIsNone(capture.authorization(self.live, self.mac, now=1001))


if __name__ == '__main__':
    unittest.main()
