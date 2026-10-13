"""Public scheduled-window and admission tests; all state/services are isolated."""
import contextlib
import fcntl
import grp
import pwd
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import backup_priority as priority
import backup_priority_control as priority_cli
import cloud_backup_control as controls
import local_fairness as fairness
import local_fairness_window as runner
from pi.tests.backup.test_backup_priority import PriorityFixture, GEN
from pi.tests.backup.test_time_machine_icloud import source_fixture
from time_machine_store import atomic_json, freeze, validate_manifest
from time_machine_verification import ObjectVerifier


class FairnessTests(PriorityFixture):
    def setUp(self):
        super().setUp()
        previous_umask = os.umask(0o077)
        os.umask(previous_umask)
        self.addCleanup(os.umask, previous_umask)
        self.ticks = 1000.0
        self.boot_id = '11111111-1111-1111-1111-111111111111'
        self.system_boot_clock = fairness.boot_clock
        boot = mock.patch.object(fairness, 'boot_clock', create=True,
            side_effect=lambda: SimpleNamespace(boot_id=self.boot_id, ticks=self.ticks))
        boot.start(); self.addCleanup(boot.stop)
        self.config = fairness.FairnessConfig()
        self.stamps = {kind: self.root / (kind.value + '.stamp') for kind in fairness.LocalJob}
        self.mac = self.jobs[priority.PriorityMode.TIME_MACHINE]
        priority.set_policy('time-machine', now=self.now - 10)
        self.calls, self.ran, self.logs = [], [], []

    def stamp(self, kind, age):
        path = self.stamps[kind]
        path.touch()
        os.utime(path, (self.now - age, self.now - age))

    def command(self, args, **kwargs):
        self.calls.append(args)
        if args[1] == 'show':
            return subprocess.CompletedProcess(args, 0, 'ActiveState=inactive\nMainPID=0\nControlPID=0\n')
        return subprocess.CompletedProcess(args, 0, '')

    def sleep(self, amount):
        self.now += amount
        self.ticks += amount

    def child(self, args, env):
        kind = next(k for k, name in runner.SCRIPTS.items() if args[0].endswith(name))
        self.ran.append(kind)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(priority_cli.main(['--admit', kind.value]), 0)
            expected_disk = 0 if priority.policy() == priority.PriorityMode.NORMAL else 1
            self.assertEqual(priority_cli.main(['--admit', 'disk']), expected_disk)
        self.stamp(kind, 0)
        return mock.Mock(wait=mock.Mock(return_value=0))

    def window(self, **kwargs):
        return runner.Window(self.stamps, self.config, command=kwargs.pop('command', self.command),
            popen=kwargs.pop('popen', self.child), clock=self.clock, sleep=self.sleep,
            log=self.logs.append, **kwargs)

    def reserve(self):
        return fairness.reserve(self.stamps, self.config, now=self.now, log=self.logs.append)

    def test_age_boundaries_strictly_older_than_threshold(self):
        for age, expected in ((86399.999, fairness.StampAge.CURRENT),
                              (86400, fairness.StampAge.CURRENT), (86400.001, fairness.StampAge.STALE)):
            self.stamp(fairness.LocalJob.BORG, age)
            self.assertEqual(fairness.stamp_age(self.stamps[fairness.LocalJob.BORG], self.now, 24), expected)
        self.stamp(fairness.LocalJob.EXFAT, 86400)
        turn = self.reserve()
        self.assertEqual(turn['jobs'], [fairness.LocalJob.BORG])

    def test_missing_needs_turn_future_unknown_logged_without_retry_loop(self):
        self.stamp(fairness.LocalJob.BORG, -3600)
        self.assertEqual(self.window().run(), 0)
        self.assertEqual(self.ran, [fairness.LocalJob.EXFAT])
        self.assertTrue(any('future-dated' in line for line in self.logs))
        self.assertEqual(fairness.status(now=self.now)['phase'], fairness.TurnPhase.COMPLETE)
        self.calls.clear()
        self.assertEqual(self.window().run(), 0)
        self.assertEqual(self.calls, [])

    def test_pi_cloud_selection_yields_without_starting_or_pausing_mac_peer(self):
        priority.set_policy('pi', now=self.now)
        self.assertEqual(self.window().run(), 0)
        selected = self.jobs[priority.PriorityMode.PI]
        mutations = [args for args in self.calls if args[1] in ('stop', 'start')]
        self.assertEqual([args[-1] for args in mutations], [selected.service, selected.service])
        self.assertEqual(priority.policy(), 'pi')

    def test_preexisting_and_new_manual_holds_survive_byte_for_byte(self):
        for preexisting in (True, False):
            with self.subTest(preexisting=preexisting):
                if fairness.read_record():
                    (self.directory / 'local-fairness.json').unlink()
                for path in self.stamps.values(): path.unlink(missing_ok=True)
                pause = self.mac.directory / 'pause.json'
                pause.unlink(missing_ok=True)
                if preexisting:
                    controls.control('pause', 'indefinite', directory=self.mac.directory,
                        service=self.mac.service, command=self.command)
                original_child = self.child
                def child(args, env):
                    if not preexisting:
                        controls.control('pause', '60', directory=self.mac.directory,
                            service=self.mac.service, now=self.now, command=self.command)
                    before = pause.read_bytes()
                    result = original_child(args, env)
                    self.assertEqual(pause.read_bytes(), before)
                    return result
                self.calls.clear()
                self.assertEqual(self.window(popen=child).run(), 0)
                self.assertTrue(controls.is_paused(self.mac.directory, self.now))
                self.assertFalse(any(args[1] == 'start' for args in self.calls))
                expected = {'paused_until': 'indefinite' if preexisting else self.now + 3600}
                self.assertEqual(json.loads(pause.read_text()), expected)

    def test_failure_and_zero_exit_deferral_back_off_and_resume_cloud(self):
        for result in (7, 0):
            with self.subTest(result=result):
                (self.directory / 'local-fairness.json').unlink(missing_ok=True)
                def failed(args, env):
                    return mock.Mock(wait=mock.Mock(return_value=result))
                self.assertEqual(self.window(popen=failed).run(), 1)
                row = fairness.status(now=self.now)
                self.assertEqual(row['phase'], fairness.TurnPhase.BACKOFF)
                self.assertEqual(row['retry_after'], self.now + 6 * 3600)
                self.assertEqual(self.calls[-1][1], 'start')
                self.calls.clear()
                self.assertEqual(self.window().run(), 0)
                self.assertEqual(self.calls, [])
                self.sleep(6 * 3600)
                self.assertIsNotNone(self.reserve())

    def test_admission_expiry_waits_for_verifier_never_runs_local_job(self):
        def stopping(args, **kwargs):
            if args[1] == 'show':
                # No uploader PID, but the service is still draining its verifier.
                return subprocess.CompletedProcess(args, 0, 'ActiveState=deactivating\nMainPID=0\nControlPID=0\n')
            return self.command(args, **kwargs)
        self.assertEqual(self.window(command=stopping).run(), 1)
        self.assertEqual(self.ran, [])
        row = fairness.status(now=self.now)
        self.assertEqual(row['phase'], fairness.TurnPhase.BACKOFF)
        self.assertIn('admission window expired', row['reason'])
        self.assertEqual(self.calls[-1][1], 'start')

    def test_waits_until_verifier_exit_then_uses_normal_admission(self):
        shows = []
        def stopping(args, **kwargs):
            if args[1] == 'show':
                shows.append(args)
                if len(shows) < 4:
                    self.assertFalse(self.ran)
                    return subprocess.CompletedProcess(args, 0, 'ActiveState=deactivating\nMainPID=0\nControlPID=19\n')
            return self.command(args, **kwargs)
        self.assertEqual(self.window(command=stopping).run(), 0)
        self.assertEqual(len(shows), 4)
        self.assertEqual(self.ran, list(fairness.LocalJob))

    def test_long_running_borg_finishes_after_deadline_and_supervisor_signal(self):
        window = self.window()
        waits = []
        def child(args, env):
            result = self.child(args, env)
            def wait(timeout):
                waits.append(timeout)
                if len(waits) < 70:
                    self.sleep(5)
                    if len(waits) == 2: window.stop()
                    raise subprocess.TimeoutExpired(args, timeout)
                self.stamp(fairness.LocalJob.BORG, 0)
                return 0
            result.wait.side_effect = wait
            return result
        window.popen = child
        self.assertEqual(window.run(), 1)
        self.assertEqual(len(waits), 70)
        self.assertEqual(self.ran, [fairness.LocalJob.BORG])
        self.assertEqual(self.calls[-1][1], 'start')

    def test_long_borg_gives_exfat_its_own_bounded_admission(self):
        window = self.window()
        waits = []
        def child(args, env):
            result = self.child(args, env)
            if args[0].endswith('pi_backup.sh'):
                def wait(timeout):
                    waits.append(timeout)
                    self.sleep(5)
                    if len(waits) < 70:
                        raise subprocess.TimeoutExpired(args, timeout)
                    self.stamp(fairness.LocalJob.BORG, 0)
                    return 0
                result.wait.side_effect = wait
            return result
        window.popen = child
        self.assertEqual(window.run(), 0)
        self.assertFalse(window.stopping)
        self.assertEqual(len(waits), 70)
        self.assertEqual(self.ran, list(fairness.LocalJob))
        row = fairness.status(now=self.now)
        self.assertEqual(row['phase'], fairness.TurnPhase.COMPLETE)
        self.assertEqual(self.calls[-1][1], 'start')

    def test_next_admission_cannot_be_extended_repeatedly_or_replay_a_job(self):
        turn = self.reserve()
        self.assertIsNone(fairness.next_admission(turn['token'], fairness.LocalJob.EXFAT, self.config))
        self.assertTrue(fairness.admit(fairness.LocalJob.BORG, turn['token']))
        self.assertIsNone(fairness.next_admission(turn['token'], fairness.LocalJob.BORG, self.config))
        renewed = fairness.next_admission(turn['token'], fairness.LocalJob.EXFAT, self.config)
        self.assertIsNotNone(renewed)
        self.sleep(30)
        self.assertIsNone(fairness.next_admission(turn['token'], fairness.LocalJob.EXFAT, self.config))
        self.assertEqual(fairness.status()['admission_remaining_seconds'], 270)

    def test_exfat_failure_after_renewal_is_not_mislabeled_as_expired_admission(self):
        def child(args, env):
            if args[0].endswith('pi_backup.sh'):
                result = self.child(args, env)
                self.sleep(self.config.admission_seconds + 1)
                return result
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertTrue(priority.allows(fairness.LocalJob.EXFAT))
            return mock.Mock(wait=mock.Mock(return_value=7))
        self.config = fairness.FairnessConfig(admission_seconds=5)
        self.assertEqual(self.window(popen=child).run(), 1)
        row = fairness.status(now=self.now)
        self.assertEqual(row['reason'], 'local attempt failed or deferred')
        self.assertEqual(row['phase'], fairness.TurnPhase.BACKOFF)
        self.assertTrue(any('failed (status 7)' in line for line in self.logs))

    def test_running_turn_cannot_admit_next_job_at_deadline(self):
        turn = self.reserve()
        self.assertTrue(fairness.admit(fairness.LocalJob.BORG, turn['token']))
        for _ in range(10):
            self.sleep(30)
            self.assertTrue(fairness.heartbeat(turn['token']))
        self.assertTrue(controls.is_paused(self.mac.directory))
        self.assertFalse(fairness.admit(fairness.LocalJob.EXFAT, turn['token']))

    def test_abandoned_renewed_hold_expires_after_backward_wall_clock_step(self):
        turn = self.reserve()
        self.assertTrue(fairness.admit(fairness.LocalJob.BORG, turn['token']))
        for _ in range(60):
            self.sleep(30)
            self.assertTrue(fairness.heartbeat(turn['token']))
        self.now -= 600
        self.assertTrue(controls.is_paused(self.mac.directory))
        self.sleep(fairness.LEASE_SECONDS)
        self.assertFalse(controls.is_paused(self.mac.directory))

    def test_previous_boot_record_never_holds_cloud_or_admits_local_job(self):
        turn = self.reserve()
        self.boot_id = '22222222-2222-2222-2222-222222222222'
        self.ticks = 5.0
        self.assertFalse(controls.is_paused(self.mac.directory))
        self.assertFalse(fairness.admit(fairness.LocalJob.BORG, turn['token']))
        self.assertFalse(fairness.heartbeat(turn['token']))

    def test_backoff_uses_elapsed_time_despite_backward_wall_clock_step(self):
        turn = self.reserve()
        fairness.finish(turn['token'], False, 'failed')
        self.now -= 600
        self.sleep(self.config.backoff_hours * 3600)
        self.assertIsNotNone(self.reserve())

    def test_reboot_with_excessive_future_backoff_clears_it_and_logs(self):
        turn = self.reserve()
        fairness.finish(turn['token'], False, 'failed')
        self.boot_id = '22222222-2222-2222-2222-222222222222'
        self.ticks = 5.0
        self.now -= 600
        self.assertIsNotNone(self.reserve())
        self.assertTrue(any('backoff' in line and 'clock' in line for line in self.logs))

    def test_success_stamp_is_recognized_after_backward_wall_clock_step(self):
        def child(args, env):
            result = self.child(args, env)
            kind = next(k for k, name in runner.SCRIPTS.items() if args[0].endswith(name))
            self.now -= 600
            self.stamp(kind, 0)
            return result
        self.assertEqual(self.window(popen=child).run(), 0)
        self.assertEqual(fairness.status()['phase'], fairness.TurnPhase.COMPLETE)

    def test_admission_deadline_ignores_forward_and_backward_wall_steps(self):
        for step in (-600, 600):
            with self.subTest(step=step):
                (self.directory / 'local-fairness.json').unlink(missing_ok=True)
                turn = self.reserve()
                self.assertTrue(fairness.admit(fairness.LocalJob.BORG, turn['token']))
                self.now += step
                for _ in range(10):
                    self.sleep(30)
                    self.assertTrue(fairness.heartbeat(turn['token']))
                self.assertTrue(controls.is_paused(self.mac.directory))
                self.assertFalse(fairness.admit(fairness.LocalJob.EXFAT, turn['token']))
                self.assertEqual(fairness.status()['admission_remaining_seconds'], 0)

    def test_reboot_preserves_only_bounded_remaining_backoff(self):
        turn = self.reserve()
        fairness.finish(turn['token'], False, 'failed')
        self.sleep(3600)
        self.boot_id = '22222222-2222-2222-2222-222222222222'
        self.ticks = 5.0
        self.assertIsNone(self.reserve())
        self.assertEqual(fairness.status()['backoff_remaining_seconds'], 5 * 3600)
        self.sleep(5 * 3600)
        self.assertIsNotNone(self.reserve())

    def test_unscoped_legacy_record_cannot_hold_or_admit_and_recovers_to_backoff(self):
        turn = self.reserve()
        legacy = {key: value for key, value in turn.items()
                  if key not in (*fairness.CLOCK_FIELDS, 'boot_id')}
        atomic_json(self.directory / 'local-fairness.json', {**legacy, 'version': 1})
        self.assertFalse(controls.is_paused(self.mac.directory))
        self.assertFalse(fairness.admit(fairness.LocalJob.BORG, turn['token']))
        self.assertIsNone(self.reserve())
        row = fairness.status()
        self.assertEqual(row['version'], 2)
        self.assertEqual(row['phase'], fairness.TurnPhase.BACKOFF)

    def test_boot_identity_and_elapsed_clock_sources_are_injectable_and_validated(self):
        path = self.root / 'boot-id'
        path.write_text(self.boot_id + '\n')
        reading = self.system_boot_clock(boot_id_path=path, elapsed=lambda: 123.5)
        self.assertEqual((reading.boot_id, reading.ticks), (self.boot_id, 123.5))
        with self.assertRaises(ValueError):
            self.system_boot_clock(boot_id_path=path, elapsed=lambda: float('nan'))
        path.write_text('unknown')
        with self.assertRaises(ValueError):
            self.system_boot_clock(boot_id_path=path, elapsed=lambda: 1)

    def test_future_tick_grant_is_retired_by_control_and_cannot_reactivate(self):
        turn = self.reserve()
        path = self.directory / 'local-fairness.json'
        before = path.read_bytes()
        self.ticks = 500
        self.assertFalse(fairness.status()['active'])
        self.assertEqual(path.read_bytes(), before)  # Status never reconciles state.
        self.assertFalse(controls.is_paused(self.mac.directory))
        self.assertEqual(fairness.read_record()['phase'], fairness.TurnPhase.BACKOFF)
        self.ticks = 1000
        self.assertFalse(controls.is_paused(self.mac.directory))
        self.assertFalse(fairness.heartbeat(turn['token']))
        self.assertFalse(fairness.admit(fairness.LocalJob.BORG, turn['token']))

    def test_future_tick_admission_rejection_permanently_retires_grant(self):
        turn = self.reserve()
        self.ticks = 500
        self.assertFalse(fairness.admit(fairness.LocalJob.BORG, turn['token']))
        self.ticks = 1000
        self.assertFalse(fairness.admit(fairness.LocalJob.BORG, turn['token']))
        self.assertFalse(controls.is_paused(self.mac.directory))

    def test_unreadable_boot_identity_status_remains_readable_and_read_only(self):
        for success in (True, False, None):
            with self.subTest(success=success):
                (self.directory / 'local-fairness.json').unlink(missing_ok=True)
                turn = self.reserve()
                if success is not None:
                    fairness.finish(turn['token'], success, 'finished')
                before = (self.directory / 'local-fairness.json').read_bytes()
                with mock.patch.object(fairness, 'boot_clock', side_effect=PermissionError('private')):
                    result = priority.status()['local_fairness']
                self.assertEqual(result['clock_status'], 'boot identity unavailable')
                self.assertIs(result['active'], None if success is None else False)
                self.assertNotIn(turn['token'], json.dumps(result))
                self.assertNotIn('private', json.dumps(result))
                self.assertEqual((self.directory / 'local-fairness.json').read_bytes(), before)

    def test_resume_timer_and_worker_cannot_consume_manual_hold_during_turn(self):
        turn = self.reserve()
        controls.control('pause', 'indefinite', directory=self.mac.directory,
                         service=self.mac.service, now=self.now, command=self.command)
        before = (self.mac.directory / 'pause.json').read_bytes()
        self.assertFalse(controls.admit_worker(self.mac.directory, self.now))
        controls.control('resume-if-due', directory=self.mac.directory,
                         service=self.mac.service, now=self.now, command=self.command)
        self.assertEqual((self.mac.directory / 'pause.json').read_bytes(), before)
        fairness.finish(turn['token'], False, 'test failure', now=self.now)
        self.assertEqual((self.mac.directory / 'pause.json').read_bytes(), before)

    def test_one_use_narrow_capability_and_read_only_observable_cli_status(self):
        turn = self.reserve()
        before = (self.directory / 'local-fairness.json').read_bytes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(priority_cli.capture, 'status', return_value={}):
            self.assertEqual(priority_cli.main(['--status']), 0)
        shown = json.loads(output.getvalue())['local_fairness']
        self.assertTrue(shown['active'])
        self.assertNotIn(turn['token'], output.getvalue())
        self.assertEqual((self.directory / 'local-fairness.json').read_bytes(), before)
        self.assertFalse(priority.allows(fairness.LocalJob.BORG))
        with mock.patch.dict(os.environ, {fairness.TOKEN_ENV: turn['token']}):
            self.assertFalse(priority.allows('disk'))  # Clone/restore admission remains denied.
            self.assertFalse(priority.allows('pi'))
            self.assertTrue(priority.allows(fairness.LocalJob.BORG))
            self.assertFalse(priority.allows(fairness.LocalJob.BORG))
        fairness.finish(turn['token'], True, 'done', now=self.now)
        self.assertFalse(fairness.admit(fairness.LocalJob.EXFAT, turn['token'], now=self.now))

    def test_abandoned_lease_expires_cloud_hold_and_next_window_records_backoff(self):
        self.reserve()
        self.assertTrue(controls.is_paused(self.mac.directory, self.now))
        self.sleep(fairness.LEASE_SECONDS)
        self.assertFalse(controls.is_paused(self.mac.directory, self.now))
        self.assertIsNone(self.reserve())
        self.assertEqual(fairness.status(now=self.now)['phase'], fairness.TurnPhase.BACKOFF)
        self.assertEqual(fairness.status(now=self.now)['retry_after'], self.now + 6 * 3600)

    def test_generation_manifest_checkpoints_and_priority_are_unchanged(self):
        source = source_fixture(self.root, now=self.now - 60)
        store = self.root / 'store'; store.mkdir()
        manifest = freeze(source, store, GEN, 72, 0, lambda: None, lambda *_: None)
        expected = validate_manifest(manifest)
        key = next(iter(expected))
        verifier = ObjectVerifier(expected, store / 'verified-objects.json')
        verifier.verify(key, {'IsDir': False, 'Size': expected[key]['bytes'], 'ModTime': 'stable', 'ID': key},
                        lambda _: expected[key])
        paths = [store / 'pending.json', store / 'verified-objects.json',
                 self.mac.directory / 'state.json', self.directory / 'priority.json']
        before = {path: path.read_bytes() for path in paths}
        self.assertEqual(self.window().run(), 0)
        self.assertEqual({path: path.read_bytes() for path in paths}, before)
        self.assertEqual(priority.policy(), 'time-machine')
        self.assertFalse(priority.status()['completed'])

    def test_invalid_config_and_record_fail_closed(self):
        for value in (0, -1, float('nan'), float('inf'), True):
            with self.assertRaises(ValueError): fairness.FairnessConfig(stale_hours=value)
        self.reserve()
        path = self.directory / 'local-fairness.json'
        path.chmod(0o644)
        with self.assertRaises(ValueError): controls.is_paused(self.mac.directory)

    def test_resume_requests_wait_for_fairness_even_without_manual_pause(self):
        turn = self.reserve()
        for action in ('resume', 'resume-if-due'):
            controls.control(action, directory=self.mac.directory, service=self.mac.service,
                             now=self.now, command=self.command)
        self.assertEqual(self.calls, [])
        self.assertFalse(controls.admit_worker(self.mac.directory, self.now))
        fairness.finish(turn['token'], False, 'failed', now=self.now)
        controls.resume_after_local_turn(directory=self.mac.directory, service=self.mac.service,
                                         now=self.now, command=self.command)
        self.assertEqual(self.calls[-1][1], 'start')

    def test_preexisting_expired_hold_is_not_rewritten_by_turn_cleanup(self):
        pause = self.mac.directory / 'pause.json'
        atomic_json(pause, {'paused_until': self.now - 60})
        original = pause.read_bytes()
        self.assertEqual(self.window().run(), 0)
        self.assertEqual(pause.read_bytes(), original)
        self.assertEqual(self.calls[-1][1], 'start')

    def test_service_probe_failure_ends_turn_and_preserves_priority(self):
        def failed_probe(args, **kwargs):
            if args[1] == 'show':
                raise subprocess.TimeoutExpired(args, 10)
            return self.command(args, **kwargs)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.window(command=failed_probe).run()
        self.assertEqual(fairness.status(now=self.now)['phase'], fairness.TurnPhase.BACKOFF)
        self.assertEqual(self.calls[-1][1], 'start')
        self.assertEqual(priority.policy(), 'time-machine')
        self.assertFalse(self.ran)

    def test_only_current_turn_token_and_selected_generation_can_admit(self):
        turn = self.reserve()
        self.assertIsNone(self.reserve())
        self.assertFalse(fairness.admit(fairness.LocalJob.BORG, 'a' * 64))
        row = priority.read_record(self.directory)
        atomic_json(self.directory / 'priority.json', {**row, 'generation': 'different'})
        self.assertFalse(fairness.admit(fairness.LocalJob.BORG, turn['token']))

    @unittest.skipUnless(sys.platform == 'linux', 'Linux shared-lock shell admission')
    def test_real_shared_lock_and_priority_cli_admit_only_scheduled_local_jobs(self):
        turn = self.reserve()
        source = Path(runner.__file__).parent
        tool = self.root / 'priority-cli.py'
        tool.write_text('import os,time\nfrom pathlib import Path\nimport backup_priority as p\n'
            'import backup_priority_control as c\nimport local_fairness as f\n'
            'root=Path(os.environ["TEST_ROOT"])\n'
            'time.time=lambda:float(os.environ["TEST_NOW"])\n'
            'f.boot_clock=lambda:f.BootClock(os.environ["TEST_BOOT_ID"],float(os.environ["TEST_TICKS"]))\n'
            'p.DIRECTORY=root/"priority"\n'
            'p.JOBS={mode:p.CloudJob(job.label,job.service,root/mode.value,root/(mode.value+".json")) '
            'for mode,job in p.JOBS.items()}\nraise SystemExit(c.main())\n')
        # Exercise the real admission functions without sourcing host secrets.
        original = (source / 'backup_conf.sh').read_text()
        host_input = '[ -f /home/pi/secrets/.bash_variables ] && . /home/pi/secrets/.bash_variables'
        self.assertEqual(original.count(host_input), 1)
        config = self.root / 'backup_conf.sh'
        config.write_text(original.replace(host_input, ': # private inputs excluded from this fixture'))
        lock = self.root / 'shared.lock'
        lock.write_text('existing verifier PID\n'); lock.chmod(0o660)
        env = {**os.environ, fairness.TOKEN_ENV: turn['token'], 'TEST_ROOT': str(self.root),
            'TEST_NOW': str(self.now), 'TEST_BOOT_ID': self.boot_id, 'TEST_TICKS': str(self.ticks),
            'CLONE_CARD_NOMINAL_GB_FILE': str(self.root / 'missing-capacity-setting'),
            'PYTHONPATH': str(source), 'VANPI_BACKUP_PRIORITY_TOOL': str(tool),
            'VANPI_BACKUP_JOB_LOCK': str(lock), 'VANPI_BACKUP_JOB_LOCK_MODE': '660',
            'VANPI_BACKUP_JOB_LOCK_STAT': '/usr/bin/stat',
            'VANPI_BACKUP_JOB_LOCK_FLOCK': '/usr/bin/flock',
            'VANPI_BACKUP_JOB_LOCK_OWNER': pwd.getpwuid(os.geteuid()).pw_name,
            'VANPI_BACKUP_JOB_LOCK_GROUP': grp.getgrgid(os.getegid()).gr_name}
        for key in ('BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS'):
            env.pop(key, None)
        def acquire(kind):
            return subprocess.run(['/bin/bash', '-c', 'source "$1"; acquire_job_lock "$2"',
                'test', str(config), kind], env=env, capture_output=True, text=True, timeout=60)
        with lock.open('r+') as inherited_verifier_lock:
            fcntl.flock(inherited_verifier_lock, fcntl.LOCK_EX)
            self.assertNotEqual(acquire('local-borg').returncode, 0)
            self.assertEqual(lock.read_text(), 'existing verifier PID\n')
            self.assertEqual(fairness.read_record()['admitted'], [])
        self.assertNotEqual(acquire('disk').returncode, 0)
        result = acquire('local-borg')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(acquire('local-borg').returncode, 0)
        self.assertEqual(acquire('local-exfat').returncode, 0)
        self.assertNotEqual(acquire('disk').returncode, 0)

    def test_busy_supervisor_gate_is_a_clean_cli_deferral(self):
        window = self.window()
        args = ['--borg-stamp', str(self.stamps[fairness.LocalJob.BORG]),
                '--exfat-stamp', str(self.stamps[fairness.LocalJob.EXFAT]),
                '--stale-hours', '24', '--admission-seconds', '300', '--backoff-hours', '6']
        with fairness.supervisor_slot(self.directory), mock.patch.object(runner, 'Window', return_value=window), \
                mock.patch.object(runner.signal, 'signal'):
            self.assertEqual(runner.main(args), 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.ran, [])

    def test_normal_priority_does_not_create_fairness_or_pause_cloud(self):
        priority.set_policy('normal')
        self.assertEqual(self.window().run(), 0)
        self.assertIsNone(fairness.status())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.ran, list(fairness.LocalJob))


class WindowEntrypointTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = Path(runner.__file__).parent
        self.window = self.root / 'backup_window.sh'
        self.window.write_bytes((self.source / 'backup_window.sh').read_bytes())
        self.window.chmod(0o700)
        self.calls = self.root / 'calls'
        self.env = {**os.environ, 'CALLS': str(self.calls), fairness.TOKEN_ENV: 'must-not-leak'}
        for key in ('BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS'):
            self.env.pop(key, None)

    def test_manual_force_cannot_reserve_a_turn_or_inherit_admission(self):
        for name in runner.SCRIPTS.values():
            path = self.root / name
            path.write_text('#!/bin/bash\nprintf "%s %s\\n" "$*" "${VANPI_LOCAL_FAIRNESS_TOKEN:-none}" >> "$CALLS"\n')
            path.chmod(0o700)
        result = subprocess.run(['/bin/bash', str(self.window), '--force'], env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls.read_text().splitlines(), ['--force none', '--force none'])

    def test_scheduled_wrapper_passes_named_configuration_and_stamps(self):
        conf = self.root / 'config.sh'
        conf.write_text('STAMP_DIR=/test/stamps\nEXFAT_SNAPSHOT_STAMP=/test/exfat\n'
                        'LOCAL_BACKUP_FAIRNESS_HOURS=24\nLOCAL_BACKUP_FAIRNESS_ADMISSION_SECONDS=300\n'
                        'LOCAL_BACKUP_FAIRNESS_BACKOFF_HOURS=6\n')
        helper = self.root / 'local_fairness_window.py'
        helper.write_text('import json,os,sys\nprint(json.dumps(sys.argv[1:]))\n'
                          'assert "VANPI_LOCAL_FAIRNESS_TOKEN" not in os.environ\n')
        result = subprocess.run(['/bin/bash', str(self.window)],
            env={**self.env, 'PI_BACKUP_CONF': str(conf)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ['--borg-stamp', '/test/stamps/borg_ok',
            '--exfat-stamp', '/test/exfat', '--stale-hours', '24', '--admission-seconds', '300',
            '--backoff-hours', '6'])

    def test_both_targeted_installers_use_complete_shared_payload(self):
        repo = self.source.parents[2]
        helper = repo / 'pi/backup_local_deploy.sh'
        result = subprocess.run(['/bin/bash', '-c', 'source "$1"; printf "%s\\n" "${LOCAL_FAIRNESS_FILES[@]}"',
                                 'test', str(helper)], check=True, capture_output=True, text=True)
        files = result.stdout.splitlines()
        self.assertEqual(set(files), {'local_fairness.py', 'local_fairness_window.py',
                                     'local_fairness_rollback.py', 'backup_window.sh',
                                     'pi_backup.sh', 'exfat_snapshot.sh', 'openwrt_backup.sh'})
        for name in files:
            self.assertTrue((self.source / name).is_file())
        installers = ('deploy_time_machine_icloud.sh', 'deploy_icloud_backup.sh')
        for name in installers:
            text = (repo / 'pi' / name).read_text()
            self.assertIn('verify_local_backup_payload', text)
            self.assertIn('"${LOCAL_FAIRNESS_FILES[@]}"', text)
            self.assertIn('"${LOCAL_FAIRNESS_FILES[@]/#/', text)
            self.assertIn('scripts/backup/protected_json.py', text)
            self.assertIn(' protected_json.py ', text)
            self.assertIn('scripts/backup/time_machine_staging.py', text)
            self.assertIn(' time_machine_staging.py ', text)
            self.assertIn('local_fairness_rollback.py', text)
            self.assertIn('"$R" --check', text)
            self.assertIn('"$R" --archive', text)
            self.assertNotIn('--previous "$P"', text)
        payload = (repo / 'pi' / installers[0]).read_text().split('COPYFILE_DISABLE=1 /usr/bin/tar', 1)[1].split('| ssh', 1)[0]
        self.assertIn('pi/tests/backup/test_protected_json.py', payload)
        self.assertIn('pi/tests/backup/test_local_fairness_rollback.py', payload)
        for name in installers:
            self.assertIn('pi/' + name, payload)  # Remote tests inspect these contracts too.


if __name__ == '__main__':
    unittest.main()
