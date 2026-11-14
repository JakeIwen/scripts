"""Dashboard persistence and read-only CLI behavior against disposable offsite sources."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import unittest

from pi.tests.backup.offsite_remote_fixtures import RemoteFixture


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.f = RemoteFixture()
        self.addCleanup(self.f.close)

    def ready(self):
        result = self.f.prepare_replayed()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.f.sources()

    def backup(self, *sources, remote=False):
        args = ['backup', '--label', self.f.label]
        if remote:
            args.append('--remote')
        for source in sources:
            args.extend(('--only', source))
        return self.f.run(*args)

    def history(self):
        return json.loads((self.f.cfg.state / 'history.json').read_text())

    def read_json(self, *args):
        result = self.f.run(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        value = json.loads(result.stdout)
        self.assertEqual(result.stdout, json.dumps(value, sort_keys=True) + '\n')
        return value

    def test_separate_units_preserve_sources_labels_and_last_ok_after_failure(self):
        self.ready()
        result = self.backup('exfat512', 'movingparts', 'borg')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        first = self.history()[self.f.label]
        history = self.history()
        history['offsite-14tb'] = first
        path = self.f.cfg.state / 'history.json'
        path.write_text(json.dumps(history))
        result = self.backup('time-machine')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(set(json.loads(result.stdout)['sources']), {'time-machine'})
        self.f.reload()
        self.f.state['backup_failure'] = True
        self.f.save()
        result = self.backup('borg')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        history = self.history()
        self.assertEqual(history['offsite-14tb'], first)
        rows = history[self.f.label]
        self.assertEqual(set(rows), set(self.f.cfg.sources()))
        self.assertEqual(rows['exfat512'], first['exfat512'])
        self.assertEqual(rows['borg']['last_ok'], first['borg']['last_ok'])
        latest = rows['borg']['latest']
        self.assertEqual(latest['result'], 'failed')
        self.assertIn('Input/output error', latest['cause'])
        self.assertEqual(latest['target_kind'], 'local')
        self.assertGreater(latest['finished'], rows['borg']['last_ok']['finished'])
        self.assertGreater(latest['duration_seconds'], 0)
        ok = rows['time-machine']['latest']
        outcome = self.read_json('status')['sources']['borg']
        self.assertEqual({k: v for k, v in latest.items() if k not in ('finished', 'target_kind')}, outcome)
        self.assertEqual(ok['snapshot'], 'c' * 64)
        self.assertEqual(ok['logical_bytes_processed'], 100)
        self.assertIsNone(ok['newly_added_packed_bytes'])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_remote_completion_and_failed_attempt_keep_receipt_and_last_ok(self):
        self.ready()
        result = self.backup('borg', remote=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        first = self.history()[self.f.label]['borg']
        self.assertEqual(first['latest'], first['last_ok'])
        self.assertEqual(first['latest']['target_kind'], 'remote')
        self.assertEqual(first['latest']['receipt'], 'd' * 64)
        self.assertEqual(first['latest']['snapshot'], 'b' * 64)
        self.f.reload()
        self.f.state['backup_failure'] = True
        self.f.save()
        result = self.backup('borg', remote=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        row = self.history()[self.f.label]['borg']
        self.assertEqual(row['last_ok'], first['last_ok'])
        self.assertEqual(row['latest']['result'], 'failed')
        self.assertEqual(row['latest']['target_kind'], 'remote')

    def test_interrupted_source_is_recorded_for_local_and_remote_runs(self):
        self.ready()
        for remote in (False, True):
            with self.subTest(remote=remote):
                started = self.f.root / 'backup-started'
                started.unlink(missing_ok=True)
                self.f.reload()
                self.f.state['hang'] = True
                self.f.save()
                args = ['backup', '--label', self.f.label, '--only', 'borg']
                child = self.f.start(*args, *(['--remote'] if remote else []))
                try:
                    deadline = time.monotonic() + 60
                    while not started.exists() and child.poll() is None:
                        self.assertLess(time.monotonic(), deadline, 'source backup never started')
                        time.sleep(0.02)
                    # An early admission exit is not readiness; otherwise history is still the prior run's.
                    if not started.exists():
                        out, err = child.communicate(timeout=60)
                        self.fail(f'source backup exited before readiness ({child.returncode}): {out}{err}')
                    child.send_signal(signal.SIGTERM)
                    out, err = child.communicate(timeout=60)
                    self.assertEqual(child.returncode, 143, out + err)
                    row = self.history()[self.f.label]['borg']
                    self.assertIsNone(row['last_ok'])
                    self.assertEqual(row['latest']['result'], 'interrupted')
                    self.assertEqual(row['latest']['target_kind'], 'remote' if remote else 'local')
                    self.assertIn('SIGTERM', row['latest']['cause'])
                finally:
                    if child.poll() is None:
                        child.terminate()
                        child.communicate(timeout=60)

    def test_invalid_or_unreadable_history_is_preserved_without_breaking_backup(self):
        self.ready()
        path = self.f.cfg.state / 'history.json'
        for text in ('not json', '[]', '{"offsite-14tb":{"borg":{"latest":{},"last_ok":null}}}'):
            with self.subTest(text=text):
                path.write_text(text)
                result = self.backup('borg')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(json.loads(result.stdout)['history_error'])
                self.assertEqual(path.read_text(), text)
                self.assertNotEqual(self.f.run('dashboard').returncode, 0)
        path.unlink()
        path.mkdir()
        result = self.backup('borg')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['history_error'])
        self.assertTrue(path.is_dir())

    def test_history_write_failure_leaves_previous_history_and_backup_success(self):
        self.ready()
        self.assertEqual(self.backup('borg').returncode, 0)
        before = (self.f.cfg.state / 'history.json').read_bytes()
        self.f.reload()
        self.f.state['history_write_failure'] = True
        self.f.save()
        result = self.backup('borg')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['history_error'], 'fixture history write failed')
        self.assertEqual((self.f.cfg.state / 'history.json').read_bytes(), before)

    def test_read_only_exports_preserve_status_and_all_evidence_without_lock_or_ignition(self):
        self.assertEqual(self.f.run('status').stdout, '{"result": "not-run"}\n')
        self.assertEqual(self.read_json('dashboard'), {'schema': 1, 'status': {'result': 'not-run'},
                                                     'controls': {'supported': False, 'reason': 'local-target'},
                                                     'availability': None,
                                                     'history': {}, 'evidence': {'check': None, 'restore_test': None}})
        status = {'schema': 1, 'result': 'running', 'bytes_done': 12, 'source': 'borg', 'extra': 'retained'}
        check = {'schema': 1, 'operation': 'check', 'label': self.f.label, 'repo_id': 'a' * 64,
                 'started': 10, 'finished': 20, 'duration_seconds': 10, 'result': 'failed',
                 'cause': 'readback failed', 'subset': '5%', 'extra': {'retained': True}}
        restore = {**check, 'operation': 'restore-test', 'receipt': 'd' * 64, 'include': '/sample'}
        for filename, value in [('status.json', status), ('remote-check.json', check),
                                ('remote-restore-test.json', restore)]:
            (self.f.cfg.state / filename).write_text(json.dumps(value))
        self.f.cfg.ignition.touch()
        before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.f.root.rglob('*') if p.is_file()}
        value = self.read_json('dashboard')
        self.assertEqual(value, {'schema': 1, 'status': status, 'history': {},
                                 'controls': {'supported': False, 'reason': 'local-target'},
                                 'availability': None,
                                 'evidence': {'check': check, 'restore_test': restore}})
        self.assertEqual(self.f.run('status').stdout, json.dumps(status, sort_keys=True) + '\n')
        for source in self.f.cfg.sources():
            self.assertEqual(self.read_json('rates', source), {'schema': 1, 'source': source, 'samples': []})
        after = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.f.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(self.f.calls(), [])
        self.assertFalse((self.f.cfg.state / 'rates').exists())

    def test_rates_reject_unknown_source_and_non_root_readers_fail_clearly(self):
        result = self.f.run('rates', '../borg')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid choice', result.stderr)
        config = json.loads(self.f.config_file.read_text())
        config['owner'] += 1
        self.f.config_file.write_text(json.dumps(config))
        for args in [('dashboard',), ('rates', 'borg')]:
            result = self.f.run(*args)
            self.assertEqual(result.returncode, 1)
            self.assertIn('as root', result.stderr)
            self.assertEqual(result.stdout, '')
        self.assertEqual(self.f.calls(), [])

    @unittest.skipIf(os.geteuid() == 0, 'shell refusal requires an unprivileged process')
    def test_shell_read_only_commands_refuse_non_root_before_loading_host_config(self):
        wrapper = Path(__file__).resolve().parents[2] / 'scripts' / 'backup' / 'offsite_backup.sh'
        for args in [('dashboard',), ('rates', 'borg')]:
            result = subprocess.run(['/bin/bash', str(wrapper), *args], capture_output=True,
                                    text=True, cwd=self.f.root, timeout=60)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
            self.assertEqual(result.stderr, 'Run offsite_backup.sh with sudo.\n')

    def test_logical_rate_samples_append_per_source_and_sampler_resets(self):
        self.ready()
        now = int(time.time()) - 1000
        self.f.state['progress_events'] = [
            {'message_type': 'status', 'bytes_done': 0, 'total_bytes': 61000, 'seconds_elapsed': 0},
            {'message_type': 'status', 'bytes_done': 61000, 'total_bytes': 61000, 'seconds_elapsed': 61}]
        self.f.state['sample_times'] = [now, now, now + 61, now + 100, now + 100, now + 161]
        self.f.save()
        result = self.backup('borg', 'exfat512')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.f.reload()
        self.f.state['sample_times'] = [now + 200, now + 200, now + 261]
        self.f.save()
        result = self.backup('borg', remote=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.f.reload()
        self.f.state.pop('sample_times')
        self.f.save()
        self.assertEqual(self.read_json('rates', 'borg'),
                         {'schema': 1, 'source': 'borg', 'samples': [[now + 61, 1000], [now + 261, 1000]]})
        self.assertEqual(self.read_json('rates', 'exfat512')['samples'], [[now + 161, 1000]])
        self.assertEqual((self.f.cfg.state / 'rates').stat().st_mode & 0o777, 0o700)

    def test_unavailable_rates_directory_does_not_fail_backup(self):
        self.ready()
        (self.f.cfg.state / 'rates').touch()
        result = self.backup('borg')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
