"""Public CLI controls and a real supervised worker; no real disks or network."""
import json
import os
import subprocess
import time
import unittest
from unittest.mock import patch

from offsite_control import control_lock, read_control, request_control, resume_backup, ControlConflict
from offsite_config import RemoteControlGuard
from offsite_remote import RemoteRepository
from offsite_repository import Commands, Interrupted
from pi.tests.backup.offsite_remote_fixtures import RemoteFixture, SECRET


class RemoteControlsTests(unittest.TestCase):
    def setUp(self):
        self.f = RemoteFixture()
        self.addCleanup(self.f.close)
        result = self.f.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.f.sources()

    def backup(self, *options):
        return self.f.run('backup', '--remote', '--label', self.f.label, '--only', 'exfat512', *options)

    def control(self, action, revision=None):
        revision = revision or read_control(self.f.cfg, self.f.label)['revision']
        return self.f.run(action, '--remote', '--label', self.f.label, '--revision', revision)

    def seed_plan(self):
        result = self.backup()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_idle_hold_survives_new_process_and_does_not_start_work_or_signal_saved_pid(self):
        self.seed_plan()
        current = self.f.cfg.state / 'status.json'
        value = json.loads(current.read_text())
        value.update(pid=os.getpid(), result='running', updated=1)
        current.write_text(json.dumps(value))
        before = len(self.f.calls())
        result = self.control('pause')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.f.calls()[before:], [])
        self.assertTrue(read_control(self.f.cfg, self.f.label)['paused'])
        refused = self.backup()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('paused', refused.stderr)
        self.assertEqual(self.f.calls()[before:], [])
        # Checks and local seeds deliberately do not consume remote holds.
        self.assertEqual(self.f.run('check', '--remote', '--label', self.f.label).returncode, 0)
        self.assertEqual(self.f.run('backup', '--label', self.f.label, '--only', 'exfat512').returncode, 0)
        self.assertTrue(read_control(self.f.cfg, self.f.label)['paused'])

    def test_local_mode_unknown_plan_and_stale_revision_refuse(self):
        self.assertNotEqual(self.control('pause', 'a' * 32).returncode, 0)
        self.seed_plan()
        revision = read_control(self.f.cfg, self.f.label)['revision']
        self.assertEqual(self.f.run('pause', '--label', self.f.label, '--revision', revision).returncode, 2)
        self.assertEqual(self.control('pause', revision).returncode, 0)
        self.assertNotEqual(self.control('pause', revision).returncode, 0)
        self.assertNotEqual(self.control('pause', '../bad').returncode, 0)

    def test_cooperative_stop_is_latched_and_reaps_real_child_without_pid_signalling(self):
        self.seed_plan()
        commands = Commands(self.f.cfg, self.f.locks())
        commands.remote_control = RemoteControlGuard(self.f.label, read_control(self.f.cfg, self.f.label)['revision'])
        # The subprocess exits only when Commands' normal cancellation path reaps it.
        script = self.f.root / 'waiting'
        script.write_text('#!/bin/sh\nprintf "started\\n"\nexec /bin/sleep 60\n')
        script.chmod(0o700)
        self.f.cfg.commands['waiting'] = str(script)
        def pause_on_output(line):
            self.assertEqual(self.control('pause').returncode, 0)
        with patch.dict(os.environ, self.f.env):
            started = time.monotonic()
            with self.assertRaises(Interrupted):
                commands.run('waiting', line=pause_on_output)
            self.assertLess(time.monotonic() - started, 10)
        self.assertIn('paused', commands.stop_cause())

    def test_running_time_machine_pause_releases_gate_and_resources_and_preserves_history(self):
        self.f.state['hold_until'] = str(self.f.root / 'never-created')
        self.f.save()
        worker = self.f.start('backup', '--remote', '--label', self.f.label, '--only', 'time-machine')
        try:
            deadline = time.monotonic() + 30
            while not (self.f.root / 'backup-started').exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue((self.f.root / 'backup-started').exists())
            self.assertTrue(self.f.cfg.drain.exists())
            result = self.control('pause')
            self.assertEqual(result.returncode, 0, result.stderr)
            out, err = worker.communicate(timeout=30)
            self.assertEqual(worker.returncode, 143, out + err)
            state = json.loads(out)
            self.assertEqual(state['sources']['time-machine']['result'], 'interrupted')
            self.assertEqual(state['target_kind'], 'remote')
            self.assertFalse(self.f.cfg.drain.exists())
            self.f.reload()
            self.assertEqual(self.f.state['busy_at_notify'], [])
            self.assertTrue(read_control(self.f.cfg, self.f.label)['paused'])
            history = json.loads((self.f.cfg.state / 'history.json').read_text())
            self.assertEqual(history[self.f.label]['time-machine']['latest']['result'], 'interrupted')
            self.assertIsNone(history[self.f.label]['time-machine']['last_ok'])
        finally:
            if worker.poll() is None:
                worker.terminate()
                worker.communicate(timeout=30)

    def test_concurrent_control_never_partially_changes_intent(self):
        self.seed_plan()
        before = read_control(self.f.cfg, self.f.label)
        with control_lock(self.f.cfg):
            result = self.control('pause')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(read_control(self.f.cfg, self.f.label), before)

    def test_resume_launch_is_one_fixed_attempt_with_hold_retained_until_admission(self):
        self.seed_plan()
        self.assertEqual(self.control('pause').returncode, 0)
        plan = read_control(self.f.cfg, self.f.label)
        calls = []
        def launch(argv, **kwargs):
            calls.append(argv)
            # The worker must not race a controller-held mutex.
            with control_lock(self.f.cfg):
                pass
            return subprocess.CompletedProcess(argv, 0, '', '')
        request_control(self.f.cfg, 'resume', self.f.label, plan['revision'], command=launch)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-6:], ['resume-run', '--remote', '--label', self.f.label,
                                       '--revision', plan['revision']])
        self.assertNotIn(SECRET, str(calls))
        self.assertEqual(read_control(self.f.cfg, self.f.label), plan)
        result = self.f.run('resume-run', '--remote', '--label', self.f.label, '--revision', plan['revision'])
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        resumed = json.loads(result.stdout)
        self.assertEqual(set(resumed['sources']), {'exfat512'})
        self.assertEqual(resumed['target_kind'], 'remote')
        self.assertFalse(read_control(self.f.cfg, self.f.label)['paused'])

    def test_guard_failure_or_new_pause_keeps_hold_and_never_writes_repository(self):
        self.seed_plan()
        self.assertEqual(self.control('pause').returncode, 0)
        for marker in ('offline', 'denied', 'wrong-route'):
            plan = read_control(self.f.cfg, self.f.label)
            path = self.f.root / marker
            path.touch()
            before = len(self.f.calls())
            result = self.f.run('resume-run', '--remote', '--label', self.f.label, '--revision', plan['revision'])
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(read_control(self.f.cfg, self.f.label), plan)
            self.assertFalse(any(row[0] == 'restic' for row in self.f.calls()[before:]))
            path.unlink()
        stale = read_control(self.f.cfg, self.f.label)['revision']
        self.assertEqual(self.control('pause').returncode, 0)
        result = self.f.run('resume-run', '--remote', '--label', self.f.label, '--revision', stale)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(read_control(self.f.cfg, self.f.label)['paused'])

    def test_pause_during_resume_preflight_wins_without_clearing_hold(self):
        self.seed_plan()
        self.assertEqual(self.control('pause').returncode, 0)
        plan = read_control(self.f.cfg, self.f.label)
        repo = RemoteRepository(self.f.cfg, Commands(self.f.cfg, self.f.locks()), self.f.label)
        verify = repo.verify
        def concurrent_pause():
            verify()
            request_control(self.f.cfg, 'pause', self.f.label, plan['revision'])
        with patch.dict(os.environ, self.f.env), patch.object(repo, 'verify', side_effect=concurrent_pause):
            with self.assertRaises(ControlConflict):
                resume_backup(repo, plan['revision'])
        self.assertTrue(read_control(self.f.cfg, self.f.label)['paused'])
        self.assertIn('paused', repo.commands.stop_cause())

    def test_unsafe_control_and_changed_identity_fail_closed(self):
        self.seed_plan()
        self.assertEqual(self.control('pause').returncode, 0)
        plan = read_control(self.f.cfg, self.f.label)
        path = self.f.cfg.state / ('remote-control-' + self.f.label + '.json')
        original = path.read_bytes()
        path.chmod(0o644)
        self.assertNotEqual(self.backup().returncode, 0)
        path.chmod(0o600)
        path.write_bytes(original)
        registry = self.f.cfg.state / 'repos.json'
        value = json.loads(registry.read_text())
        value['repos'][self.f.label]['repo_id'] = 'f' * 64
        registry.write_text(json.dumps(value))
        with self.assertRaises(ControlConflict):
            request_control(self.f.cfg, 'resume', self.f.label, plan['revision'])


if __name__ == '__main__':
    unittest.main()
