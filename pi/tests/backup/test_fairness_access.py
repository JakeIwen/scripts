"""Fairness namespace and non-owner boundaries, using only temporary state."""
import os
from pathlib import Path
import unittest
from unittest import mock

import backup_priority as priority
import cloud_backup_control as controls
import local_fairness as fairness
from pi.tests.backup.priority_fixtures import PriorityFixture
from time_machine_store import atomic_json


class FairnessAccessTests(PriorityFixture):
    def setUp(self):
        super().setUp()
        self.cloud = self.jobs[priority.PriorityMode.TIME_MACHINE]
        self.stamps = {kind: self.root / kind.value for kind in fairness.LocalJob}
        self.reading = fairness.BootClock('11111111-1111-1111-1111-111111111111', 1000)
        clock = mock.patch.object(fairness, 'boot_clock', side_effect=lambda: self.reading)
        clock.start(); self.addCleanup(clock.stop)
        priority.set_policy('time-machine', directory=self.directory, jobs=self.jobs, now=self.now - 10)
        self.command = mock.Mock()

    def poison_default(self):
        poison = self.root / 'forbidden-default'
        original = Path.lstat
        def checked(path):
            if path == poison or poison in path.parents:
                raise AssertionError('Fixture operation reached default priority storage')
            return original(path)
        return mock.patch.object(priority, 'DIRECTORY', poison), mock.patch.object(Path, 'lstat', checked)

    def foreign_owner(self, target):
        original = Path.lstat
        def inspect(path):
            result = original(path)
            if path == target:
                fields = list(result); fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return result
        return mock.patch.object(Path, 'lstat', inspect)

    def test_unregistered_cloud_directory_does_not_read_host_priority_state(self):
        cloud = self.root / 'independent-cloud'; cloud.mkdir()
        namespace, guard = self.poison_default()
        with namespace, guard:
            self.assertFalse(controls.is_paused(cloud))
            self.assertTrue(controls.admit_worker(cloud))
            atomic_json(cloud / 'pause.json', {'paused_until': 'indefinite'})
            self.assertTrue(controls.is_paused(cloud))
            (cloud / 'pause.json').unlink()
            atomic_json(cloud / 'state.json', {'phase': 'deferred', 'retry_at': self.now - 1})
            controls.control('resume-if-due', directory=cloud, service=self.cloud.service,
                             command=self.command)
            self.assertEqual(self.command.call_args.args[0][1], 'start')

    def test_explicit_namespace_flows_through_all_fairness_operations(self):
        namespace, guard = self.poison_default()
        with namespace, guard:
            turn = fairness.reserve(self.stamps, fairness.FairnessConfig(),
                directory=self.directory, jobs=self.jobs, now=self.now, log=lambda _: None)
            self.assertTrue(fairness.cloud_held(self.cloud.directory, directory=self.directory, jobs=self.jobs))
            self.assertTrue(fairness.status(self.directory)['active'])
            self.assertTrue(fairness.heartbeat(turn['token'], directory=self.directory))
            self.assertTrue(fairness.admit(fairness.LocalJob.BORG, turn['token'],
                directory=self.directory, jobs=self.jobs))
            fairness.finish(turn['token'], False, 'failed', directory=self.directory)
            self.assertIsNone(fairness.reserve(self.stamps, fairness.FairnessConfig(),
                directory=self.directory, jobs=self.jobs, log=lambda _: None))
            self.assertFalse(fairness.cloud_held(self.cloud.directory, directory=self.directory, jobs=self.jobs))

    def test_cloud_controls_forward_explicit_priority_namespace(self):
        turn = fairness.reserve(self.stamps, fairness.FairnessConfig(), log=lambda _: None)
        namespace, guard = self.poison_default()
        options = dict(priority_directory=self.directory, jobs=self.jobs)
        with namespace, guard:
            self.assertTrue(controls.is_paused(self.cloud.directory, **options))
            self.assertFalse(controls.admit_worker(self.cloud.directory, **options))
            controls.pause_for_local_turn(directory=self.cloud.directory, service=self.cloud.service,
                                         command=self.command, **options)
            controls.control('resume-if-due', directory=self.cloud.directory, service=self.cloud.service,
                             command=self.command, **options)
            self.assertEqual(self.command.call_count, 1)  # Only the verified graceful pause.
            fairness.finish(turn['token'], True, 'done', directory=self.directory)
            controls.resume_after_local_turn(directory=self.cloud.directory, service=self.cloud.service,
                                             command=self.command, **options)
            self.assertEqual(self.command.call_args.args[0][1], 'start')

    def test_future_grant_reconciliation_writes_only_the_explicit_namespace(self):
        turn = fairness.reserve(self.stamps, fairness.FairnessConfig(), log=lambda _: None)
        self.reading = fairness.BootClock(self.reading.boot_id, 500)
        namespace, guard = self.poison_default()
        with namespace, guard:
            self.assertFalse(fairness.cloud_held(self.cloud.directory, directory=self.directory, jobs=self.jobs))
            row = fairness.read_record(self.directory)
            self.assertEqual(row['token'], turn['token'])
            self.assertEqual(row['phase'], fairness.TurnPhase.BACKOFF)

    def test_non_owner_pause_checks_fail_closed_and_status_is_readable(self):
        fairness.reserve(self.stamps, fairness.FairnessConfig(), log=lambda _: None)
        pause = self.cloud.directory / 'pause.json'
        atomic_json(pause, {'paused_until': self.now - 1})
        before = pause.read_bytes()
        with self.foreign_owner(self.directory):
            self.assertTrue(controls.is_paused(self.cloud.directory))
            self.assertFalse(controls.admit_worker(self.cloud.directory))
            self.assertEqual(pause.read_bytes(), before)
            result = fairness.status(self.directory)
            self.assertIsNone(result['active'])
            self.assertEqual(result['state_status'], 'unavailable')
            with self.assertRaises(ValueError): fairness.read_record(self.directory)

    def test_non_owner_automatic_resume_is_blocked_before_priority_retry_reads(self):
        fairness.reserve(self.stamps, fairness.FairnessConfig(), log=lambda _: None)
        with self.foreign_owner(self.directory):
            result = controls.control('resume-if-due', directory=self.cloud.directory,
                                      service=self.cloud.service, command=self.command)
        self.assertEqual(result, {'ok': True, 'changed': False})
        self.command.assert_not_called()

    def test_unknown_fairness_state_never_authorizes_a_cloud_stop(self):
        fairness.reserve(self.stamps, fairness.FairnessConfig(), log=lambda _: None)
        with self.foreign_owner(self.directory), self.assertRaises(ValueError):
            controls.pause_for_local_turn(directory=self.cloud.directory, service=self.cloud.service,
                                         command=self.command)
        self.command.assert_not_called()

    def test_permission_failure_blocks_admission_without_hiding_owner_corruption(self):
        pause = self.cloud.directory / 'pause.json'
        atomic_json(pause, {'paused_until': self.now - 1})
        before = pause.read_bytes()
        with mock.patch.object(fairness, 'read_record', side_effect=PermissionError('private path')):
            self.assertTrue(controls.is_paused(self.cloud.directory))
            self.assertFalse(controls.admit_worker(self.cloud.directory))
            self.assertEqual(fairness.status(self.directory)['state_status'], 'unavailable')
            controls.resume_after_local_turn(directory=self.cloud.directory, service=self.cloud.service,
                                             command=self.command)
        self.command.assert_not_called()
        self.assertEqual(pause.read_bytes(), before)
        with mock.patch.object(fairness, 'read_record', side_effect=ValueError('invalid owner record')):
            with self.assertRaises(ValueError): controls.is_paused(self.cloud.directory)
            with self.assertRaises(ValueError): fairness.status(self.directory)

    def test_manual_switch_keeps_the_explicit_namespace(self):
        namespace, guard = self.poison_default()
        with namespace, guard:
            controls.take_turn(self.cloud.service, jobs={job.service: job.directory for job in self.jobs.values()},
                priority_directory=self.directory, priority_jobs=self.jobs, command=self.command)
        self.assertEqual(self.command.call_args.args[0][1], 'start')

    def test_owner_integrity_failures_remain_strict(self):
        fairness.reserve(self.stamps, fairness.FairnessConfig(), log=lambda _: None)
        path = self.directory / 'local-fairness.json'
        with self.foreign_owner(path):
            with self.assertRaises(ValueError): controls.is_paused(self.cloud.directory)
            with self.assertRaises(ValueError): fairness.status(self.directory)
        path.chmod(0o644)
        with self.assertRaises(ValueError): controls.is_paused(self.cloud.directory)
        with self.assertRaises(ValueError): fairness.status(self.directory)


if __name__ == '__main__':
    unittest.main()
