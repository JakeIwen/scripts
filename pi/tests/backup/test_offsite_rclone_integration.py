"""Opt-in real restic + rclone check for the rclone transport's two assumptions.

Set OFFSITE_RESTIC and OFFSITE_RCLONE to the pinned binaries (the Linux lane does).
It proves restic's `rclone:` backend accepts rclone's on-the-fly remote form with no
config file, and that restic hands its environment to the rclone child, so backend
options given only as RCLONE_* variables reach rclone.
"""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import subprocess
import tempfile
import time
import unittest

RESTIC = os.environ.get('OFFSITE_RESTIC')
RCLONE = os.environ.get('OFFSITE_RCLONE')
TIMEOUT = 120


@unittest.skipUnless(RESTIC and RCLONE, 'opt-in acceptance: set both OFFSITE_RESTIC and OFFSITE_RCLONE')
class RcloneEnvTransportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.repo = root / 'repo'
        self.repo.mkdir()
        self.src = root / 'src'
        self.src.mkdir()
        (self.src / 'a.txt').write_text('alpha\n')
        (self.src / 'b.txt').write_text('beta\n')
        self.password = root / 'password'
        self.password.write_text('test-password\n')
        self.password.chmod(0o600)
        self.cache_home = root / 'home'
        self.cache_home.mkdir()
        # A private copy at a path no other run shares: restic starts this one, and the
        # lifetime test finds and stops only processes started from it (the shared binary
        # may be serving unrelated tests or other lane runs).
        (root / 'bin').mkdir()
        self.rclone = str(root / 'bin' / 'rclone-private')
        shutil.copy2(RCLONE, self.rclone)
        self.addCleanup(self.stop_private_rclones)

    def stop_private_rclones(self):
        # Unconditional and bounded: never leave a throttled server running.
        deadline = time.monotonic() + 10
        while self.rclone_pids() and time.monotonic() < deadline:
            for pid in self.rclone_pids():
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            time.sleep(0.05)

    def env(self, with_remote=True, repo=None):
        # Scrubbed so only the variables set here can carry the repository location.
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('RCLONE_', 'RESTIC_'))}
        env['HOME'] = str(self.cache_home)
        env.pop('XDG_CONFIG_HOME', None)  # rclone would look there before HOME
        env['RCLONE_CONFIG'] = ''
        if with_remote:
            env['RCLONE_ALIAS_REMOTE'] = str(repo or self.repo)
        return env

    def argv(self, *args, options=(), repository='rclone::alias:'):
        return [RESTIC, '-r', repository, '-o', 'rclone.program=' + self.rclone,
                *options, '--password-file', str(self.password), '--no-cache', *args]

    def restic(self, *args, with_remote=True, options=(), env=None, repository='rclone::alias:'):
        return subprocess.run(
            self.argv(*args, options=options, repository=repository), env=env or self.env(with_remote),
            cwd=str(self.cache_home), capture_output=True, text=True, timeout=TIMEOUT,
            check=False)

    def run_ok(self, *args, **kwargs):
        result = self.restic(*args, **kwargs)
        self.assertEqual(result.returncode, 0, ' '.join(args) + '\n' + result.stdout + result.stderr)
        return result

    def test_environment_only_rclone_remote_round_trips(self):
        self.run_ok('init')
        self.run_ok('backup', str(self.src))
        config = json.loads(self.run_ok('cat', 'config').stdout)
        self.assertRegex(config['id'], re.compile(r'^[0-9a-f]{64}$'))
        snapshots = json.loads(self.run_ok('snapshots', '--json').stdout)
        self.assertEqual(len(snapshots), 1)
        self.assertTrue((self.repo / 'config').is_file())
        self.assertTrue((self.repo / 'keys').is_dir())

    def test_a_second_init_says_what_init_remote_recognizes(self):
        # init-remote turns exactly these words into its --adopt hint (plan review R1).
        from offsite_remote_init import ALREADY_INITIALIZED
        self.run_ok('init')
        again = self.restic('init')
        self.assertNotEqual(again.returncode, 0)
        self.assertTrue(any(words in again.stdout + again.stderr for words in ALREADY_INITIALIZED),
                        again.stdout + again.stderr)

    def test_without_the_environment_variable_init_fails(self):
        result = self.restic('init', with_remote=False)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.repo / 'config').exists(), result.stderr)

    def test_user_rclone_conf_cannot_add_or_redirect_remotes(self):
        # The on-the-fly :alias: remote never reads a config section, so the poison is a NAMED
        # remote: restic -r rclone:poison: works only if rclone loaded the user's config file.
        decoy = self.cache_home / 'decoy'
        decoy.mkdir()
        conf = self.cache_home / '.config' / 'rclone' / 'rclone.conf'
        conf.parent.mkdir(parents=True)
        conf.write_text('[poison]\ntype = alias\nremote = {}\n'.format(decoy))
        loaded = self.env(with_remote=False)
        del loaded['RCLONE_CONFIG']
        control = self.restic('init', env=loaded, repository='rclone:poison:')
        self.assertEqual(control.returncode, 0, 'the poison does not take effect, so this test proves nothing:\n'
                         + control.stdout + control.stderr)
        self.assertTrue((decoy / 'config').is_file())
        shutil.rmtree(decoy)
        decoy.mkdir()
        isolated = self.restic('init', with_remote=False, repository='rclone:poison:')  # RCLONE_CONFIG=''
        self.assertNotEqual(isolated.returncode, 0, isolated.stdout + isolated.stderr)
        self.assertEqual(list(decoy.iterdir()), [])
        self.assertEqual(list(self.repo.iterdir()), [])

    def test_restore_returns_the_source_bytes(self):
        self.run_ok('init')
        self.run_ok('backup', str(self.src))
        target = self.cache_home / 'restored'
        self.run_ok('restore', 'latest', '--target', str(target))
        for name in ('a.txt', 'b.txt'):
            found = list(target.rglob(name))
            self.assertEqual(len(found), 1, name)
            self.assertEqual(found[0].read_bytes(), (self.src / name).read_bytes())

    def test_client_side_append_only_blocks_forget_but_not_unlock(self):
        # The exact string the transport will use; restic.rclone.args replaces the default.
        opts = ('-o', 'rclone.args=serve restic --stdio --append-only')
        self.run_ok('init', options=opts)
        self.run_ok('backup', str(self.src), options=opts)
        listed = json.loads(self.run_ok('snapshots', '--json', options=opts).stdout)
        self.assertEqual(len(listed), 1)
        forget = self.restic('forget', listed[0]['id'], options=opts)
        self.assertNotEqual(forget.returncode, 0, forget.stdout + forget.stderr)
        after = json.loads(self.run_ok('snapshots', '--json', options=opts).stdout)
        self.assertEqual([x['id'] for x in after], [listed[0]['id']])
        self.run_ok('unlock', options=opts)

    def stop_restic(self, restic):
        try:
            os.killpg(restic.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # already gone
        restic.wait(timeout=10)

    def rclone_pids(self):
        found = subprocess.run(['pgrep', '-f', '^' + re.escape(self.rclone) + ' serve restic --stdio'],
                               capture_output=True, text=True, timeout=10, check=False)
        return found.stdout.split()

    def test_rclone_exits_when_restic_is_killed(self):
        self.run_ok('init')
        big = self.src / 'big.bin'
        big.write_bytes(os.urandom(64 * 1024 * 1024))
        # Throttled so the upload outlasts the kill; rclone sits in its own process group
        # (restic's Setpgid), so killing restic's group must not take it along by itself.
        restic = subprocess.Popen(
            self.argv('backup', '--limit-upload', '1024', str(self.src)), env=self.env(),
            cwd=str(self.cache_home), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        self.addCleanup(self.stop_restic, restic)
        deadline = time.monotonic() + 30
        while not self.rclone_pids() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(self.rclone_pids(), 'rclone never started')
        spawned = self.rclone_pids()
        os.killpg(restic.pid, signal.SIGKILL)
        killed = time.monotonic()
        while self.rclone_pids() and time.monotonic() - killed < 5:
            time.sleep(0.05)
        left = self.rclone_pids()  # teardown kills any survivor
        self.assertEqual(left, [], 'rclone {} outlived restic by over 5 s'.format(spawned))
        print('rclone exited {:.2f}s after restic was killed'.format(time.monotonic() - killed),
              file=sys.stderr)


if __name__ == '__main__':
    unittest.main()
