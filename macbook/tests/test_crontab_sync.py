"""Isolated CLI tests for revision-checked tracked crontab editing."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

REPOSITORY = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY / 'macbook/scripts/crontab_sync.py'
PYTHON = Path('/usr/bin/python3')
GIT = Path('/usr/bin/git')
OFF_PREFIX = '#off '
ACTIVE_JOB = '0 7 * * * /fixture/job >> /fixture/job.log 2>&1'


class CrontabSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.worktree = self.root / 'repo'
        self.crontab = self.worktree / 'macbook/crontab'
        self.state = self.root / 'state'
        self.live = self.root / 'live-crontab'
        self.binary = self.root / 'fake-crontab'
        self.home = self.root / 'home'
        self.crontab.parent.mkdir(parents=True)
        self.home.mkdir()
        self.binary.write_text(
            '#!/bin/sh\n'
            'set -eu\n'
            'if [ "$1" = "-l" ]; then\n'
            '  if [ -f "$FAKE_CRONTAB_LIVE" ]; then /bin/cat "$FAKE_CRONTAB_LIVE"; exit 0; fi\n'
            '  echo "crontab: no crontab for fixture" >&2\n'
            '  exit 1\n'
            'fi\n'
            'if [ "${FAKE_CRONTAB_FAIL_INSTALL:-0}" = 1 ]; then\n'
            '  echo "fixture install failed" >&2\n'
            '  exit 1\n'
            'fi\n'
            '/bin/cp "$1" "$FAKE_CRONTAB_LIVE"\n')
        self.binary.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update({
            'HOME': str(self.home),
            'CRONTAB_SYNC_FILE': str(self.crontab),
            'CRONTAB_SYNC_STATE_DIR': str(self.state),
            'CRONTAB_SYNC_CRONTAB': str(self.binary),
            'FAKE_CRONTAB_LIVE': str(self.live),
        })
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.set_crontab(ACTIVE_JOB + '\n')

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args):
        result = subprocess.run((str(GIT), '-C', str(self.worktree), *args),
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def set_crontab(self, text, live=None, mode=0o644):
        self.crontab.write_text(text)
        self.crontab.chmod(mode)
        self.live.write_text(text if live is None else live)
        self.git('add', 'macbook/crontab')
        self.git('commit', '-qm', 'fixture', '--allow-empty')

    def run_cli(self, *args, env=None):
        return subprocess.run((str(PYTHON), '-B', str(SCRIPT), *args),
                              capture_output=True, text=True, check=False,
                              env=env or self.env)

    def result_json(self, *args, expected=0, env=None):
        result = self.run_cli(*args, env=env)
        self.assertEqual(result.returncode, expected, result.stderr or result.stdout)
        return json.loads(result.stdout)

    @staticmethod
    def job_id(line):
        return hashlib.sha256(line.strip().encode()).hexdigest()[:12]

    def test_status_lists_active_paused_and_managed_jobs(self):
        codex = '0 21 * * 0 /fixture/codex'
        restore = '* * * * * /fixture/restore'
        text = (f'{ACTIVE_JOB}\n#off 30 8 * * * /fixture/paused\n'
                f'# BEGIN managed codex-release-cleanup\n{codex}\n'
                '# END managed codex-release-cleanup\n'
                f'# BEGIN managed llm-restore\n{restore}\n# END managed llm-restore\n')
        self.set_crontab(text)
        value = self.result_json('status', '--json')
        self.assertEqual(set(value), {'revision', 'uncommitted', 'jobs', 'live_only',
                                     'repo_only', 'installable'})
        self.assertFalse(value['uncommitted'])
        self.assertTrue(value['installable'])
        self.assertEqual(value['live_only'], [])
        self.assertEqual(len(value['jobs']), 4)
        self.assertEqual(value['jobs'][0]['schedule_fields'], ['0', '7', '*', '*', '*'])
        self.assertFalse(value['jobs'][0]['paused'])
        self.assertTrue(value['jobs'][1]['paused'])
        self.assertEqual(value['jobs'][2]['managed_block'], 'codex-release-cleanup')
        self.assertIsNone(value['jobs'][2]['managed_switch'])
        self.assertEqual(value['jobs'][3]['managed_switch'], 'llmrestore --cron')
        self.crontab.write_text(text + '\n')
        self.assertTrue(self.result_json('status', '--json')['uncommitted'])

    def test_status_preserves_descriptions_and_expands_log_variables(self):
        text = ('scripts=/fixture\n# Cleanup description\n'
                '0 4 * * * $scripts/cleanup >> "$scripts/cleanup.log" 2>&1\n')
        self.set_crontab(text)
        job = self.result_json('status', '--json')['jobs'][0]
        self.assertEqual(job['description'], 'Cleanup description')
        self.assertEqual(job['command'], '$scripts/cleanup >> "$scripts/cleanup.log" 2>&1')
        self.assertEqual(job['log_path'], '/fixture/cleanup.log')

    def test_pause_and_resume_preserve_line_mode_and_install(self):
        self.set_crontab(ACTIVE_JOB + '\n', mode=0o640)
        status_value = self.result_json('status', '--json')
        job_id = status_value['jobs'][0]['id']
        paused = self.result_json('pause', job_id, '--revision', status_value['revision'], '--json')
        self.assertEqual(self.crontab.read_text(), OFF_PREFIX + ACTIVE_JOB + '\n')
        self.assertEqual(self.live.read_text(), self.crontab.read_text())
        self.assertEqual(stat.S_IMODE(self.crontab.stat().st_mode), 0o640)
        self.assertTrue(paused['uncommitted'])
        backups = sorted(self.state.glob('live-*.crontab'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), ACTIVE_JOB + '\n')
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
        resumed = self.result_json('resume', job_id, '--revision', paused['revision'], '--json')
        self.assertIn('resumed', resumed['message'])
        self.assertEqual(self.crontab.read_text(), ACTIVE_JOB + '\n')
        self.assertEqual(self.live.read_text(), ACTIVE_JOB + '\n')

    def test_live_only_lines_refuse_install_and_edit_without_touching_file(self):
        live_only = '5 5 * * * /fixture/live-only'
        self.set_crontab(ACTIVE_JOB + '\n', live=ACTIVE_JOB + '\n' + live_only + '\n')
        before = self.crontab.read_bytes()
        value = self.result_json('status', '--json')
        self.assertFalse(value['installable'])
        self.assertEqual(value['live_only'], [live_only])
        refused = self.result_json('pause', value['jobs'][0]['id'], '--revision',
                                   value['revision'], '--json', expected=3)
        self.assertEqual(refused['live_only'], [live_only])
        self.assertEqual(self.crontab.read_bytes(), before)
        install = self.result_json('install', '--revision', value['revision'], '--json', expected=3)
        self.assertEqual(install['live_only'], [live_only])
        self.assertFalse(list(self.state.glob('live-*.crontab')))

    def test_revision_conflict_and_busy_lock_exit_tempfail(self):
        conflict = self.result_json('pause', self.job_id(ACTIVE_JOB), '--revision', '0' * 64,
                                    '--json', expected=75)
        self.assertIn('changed', conflict['error'])
        self.state.mkdir(exist_ok=True)
        lock = (self.state / 'lock').open('w')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            busy = self.result_json('install', '--json', expected=75)
            self.assertIn('running', busy['error'])
            self.assertIn('revision', self.result_json('status', '--json'))
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def test_managed_unknown_and_duplicate_ids_are_refused(self):
        restore = '* * * * * /fixture/restore'
        managed = f'# BEGIN managed llm-restore\n{restore}\n# END managed llm-restore\n'
        self.set_crontab(managed)
        value = self.result_json('status', '--json')
        refused = self.result_json('pause', value['jobs'][0]['id'], '--revision', value['revision'],
                                   '--json', expected=3)
        self.assertIn('llmrestore --cron off', refused['error'])
        unknown = self.result_json('pause', 'abcdefabcdef', '--revision', value['revision'],
                                   '--json', expected=3)
        self.assertIn('Unknown', unknown['error'])
        duplicate = ACTIVE_JOB + '\n' + ACTIVE_JOB + '\n'
        self.set_crontab(duplicate)
        value = self.result_json('status', '--json')
        ambiguous = self.result_json('pause', value['jobs'][0]['id'], '--revision', value['revision'],
                                     '--json', expected=3)
        self.assertIn('Ambiguous', ambiguous['error'])
        self.assertEqual(self.crontab.read_text(), duplicate)

    def test_safe_install_adds_repo_lines_and_retains_twenty_backups(self):
        added = '15 9 * * * /fixture/new-job'
        self.set_crontab(ACTIVE_JOB + '\n' + added + '\n', live=ACTIVE_JOB + '\n')
        value = self.result_json('status', '--json')
        self.assertEqual(value['repo_only'], [added])
        installed = self.result_json('install', '--revision', value['revision'], '--json')
        self.assertEqual(installed['message'], 'Installed macbook/crontab')
        self.assertEqual(self.live.read_text(), self.crontab.read_text())
        for _ in range(21):
            self.result_json('install', '--json')
        self.assertEqual(len(list(self.state.glob('live-*.crontab'))), 20)

    def test_failed_install_restores_the_tracked_edit(self):
        value = self.result_json('status', '--json')
        failed_env = {**self.env, 'FAKE_CRONTAB_FAIL_INSTALL': '1'}
        result = self.run_cli('pause', value['jobs'][0]['id'], '--revision', value['revision'],
                              '--json', env=failed_env)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(json.loads(result.stdout)['error'], 'fixture install failed')
        self.assertEqual(self.crontab.read_text(), ACTIVE_JOB + '\n')
        self.assertEqual(self.live.read_text(), ACTIVE_JOB + '\n')

    def test_usage_exit_is_two(self):
        self.assertEqual(self.run_cli().returncode, 2)


if __name__ == '__main__':
    unittest.main()
