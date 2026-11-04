"""Black-box tests of run_tests.py: a copy runs in a throwaway git repo."""
from pathlib import Path
import json
import os
import shutil
import signal
import fcntl
import subprocess
import sys
import tempfile
import time
import unittest


RUNNER = Path(__file__).resolve().parents[1] / 'run_tests.py'
# Fake tests log to a file outside the repo so logging never changes the fingerprint.
FAKE_TEST = '''\
import os, time, unittest
class T(unittest.TestCase):
    def test_it(self):
        with open(os.environ['FAKE_COUNTER'], 'a') as log:
            log.write('{name}\\n')
        print('output-{name}', flush=True)
        time.sleep({sleep})
        if {fail} or os.environ.get('FAKE_FAIL') == '1':
            self.fail('boom')
        target = os.environ.get('FAKE_EDIT')
        if target:
            with open(target, 'a') as handle:
                handle.write('edited\\n')
'''


class RunnerCase(unittest.TestCase):
    def setUp(self):
        scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, scratch, True)
        self.repo = scratch / 'repo'
        self.counter = scratch / 'counter.log'
        self.counter.touch()
        (self.repo / 'tests').mkdir(parents=True)
        shutil.copy(RUNNER, self.repo / 'run_tests.py')
        (self.repo / 'data.txt').write_text('one\n')
        self.git('init', '-q')

    def git(self, *args, cwd=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
                              cwd=cwd or self.repo, check=True, capture_output=True, env=env).stdout

    def add_tests(self, **specs):
        for name, (sleep, fail) in specs.items():
            body = FAKE_TEST.format(name=name, sleep=sleep, fail=fail)
            (self.repo / 'tests' / f'test_{name}.py').write_text(body)
        self.git('add', '-A')
        self.git('commit', '-qm', 'tests')

    def start(self, *args, cwd=None, **extra_env):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('GIT_') and key != 'PYTHONPATH'}
        env.update(FAKE_COUNTER=str(self.counter), PYTHONDONTWRITEBYTECODE='1', BTT_LIVE_TESTS='')
        env.update(extra_env)
        process = subprocess.Popen([sys.executable, str(self.repo / 'run_tests.py'), *args],
                                   cwd=cwd or self.repo, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True)
        self.addCleanup(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        process.communicate(timeout=10)

    def wait_for(self, predicate, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(0.02)
        self.fail('Fixture did not reach the expected state')

    def assert_uncached(self, **extra_env):
        before = len(self.executions())
        code, output = self.run_suite(**extra_env)
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.executions()), before + 1, output)
        self.assertNotIn('(cached:', output)
        return output

    def run_suite(self, *args, **extra_env):
        process = self.start(*args, **extra_env)
        output, _ = process.communicate(timeout=60)
        return process.returncode, output

    def executions(self):
        return self.counter.read_text().split()


class RunnerTests(RunnerCase):
    def test_second_run_is_cached(self):
        self.add_tests(a=(0, False), b=(0, False))
        self.assertEqual(self.run_suite()[0], 0)
        self.assertEqual(sorted(self.executions()), ['a', 'b'])
        code, output = self.run_suite()
        self.assertEqual(code, 0)
        self.assertIn('PASS: all 2 suite commands completed (cached', output)
        self.assertEqual(len(self.executions()), 2)

    def test_edit_invalidates_cache(self):
        self.add_tests(a=(0, False))
        self.run_suite()
        (self.repo / 'data.txt').write_text('two\n')
        self.assertEqual(self.run_suite()[0], 0)
        self.assertEqual(len(self.executions()), 2)

    def test_assume_unchanged_file_still_invalidates_cache(self):
        self.add_tests(a=(0, False))
        self.assertEqual(self.run_suite()[0], 0)
        self.git('update-index', '--assume-unchanged', 'data.txt')
        (self.repo / 'data.txt').write_text('two\n')
        code, output = self.run_suite()
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.executions()), 2, output)

    def test_ignored_dependencies_invalidate_cache(self):
        self.add_tests(a=(0, False))
        (self.repo / '.gitignore').write_text('.local/\nnode_modules/\n')
        dependency = self.repo / '.local/test-venv/lib/dependency.py'
        dependency.parent.mkdir(parents=True)
        dependency.write_text('one')
        (self.repo / 'package.json').write_text('{}')
        node = self.repo / 'node_modules/dependency/index.js'
        node.parent.mkdir(parents=True)
        node.write_text('one')
        self.assertEqual(self.run_suite()[0], 0)
        for path in (dependency, node):
            with self.subTest(path=path):
                before = len(self.executions())
                path.write_text('two')
                code, output = self.run_suite()
                self.assertEqual(code, 0, output)
                self.assertEqual(len(self.executions()), before + 1, output)

    def test_symlink_target_invalidates_cache(self):
        target = self.repo.parent / 'target.txt'
        target.write_text('one')
        (self.repo / 'link').symlink_to(target)
        self.add_tests(a=(0, False))
        self.assertEqual(self.run_suite()[0], 0)
        target.write_text('two')
        code, output = self.run_suite()
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.executions()), 2, output)

    def test_bad_json_shapes_are_discarded(self):
        self.add_tests(a=(0, False))
        state = self.repo / '.git/run_tests'
        state.mkdir()
        for value in ('[]', 'null', '{"tests/test_a.py": "wrong"}', 'not json'):
            with self.subTest(value=value):
                for name in ('passes.json', 'timings.json'):
                    (state / name).write_text(value)
                code, output = self.run_suite()
                self.assertEqual(code, 0, output)
                self.assertIn('PASS: all 1 suite commands completed.', output)

    def test_discovery_is_refreshed_after_waiting_for_lock(self):
        self.add_tests(a=(0, False))
        state = self.repo / '.git/run_tests'
        state.mkdir()
        with (state / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = self.start()
            # The message is flushed immediately before the blocking flock.
            self.assertIn('Waiting for another', process.stdout.readline())
            (self.repo / 'tests/test_new.py').write_text(
                FAKE_TEST.format(name='new', sleep=0, fail=False))
            fcntl.flock(lock, fcntl.LOCK_UN)
        output, _ = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 0, output)
        self.assertEqual(sorted(self.executions()), ['a', 'new'], output)

    def test_waiting_process_cannot_certify_a_new_runner_version(self):
        self.add_tests(a=(0, False))
        state = self.repo / '.git/run_tests'
        state.mkdir()
        with (state / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = self.start()
            self.assertIn('Waiting for another', process.stdout.readline())
            with (self.repo / 'run_tests.py').open('a') as runner:
                runner.write('\n# A new runner version.\n')
            fcntl.flock(lock, fcntl.LOCK_UN)
        output, _ = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 0, output)
        self.assertIn('runner source changed since launch', output)
        self.assertIn('not cached', output)
        self.assert_uncached()

    def test_staged_then_modified_deleted_and_renamed_inputs(self):
        self.add_tests(a=(0, False))
        self.assert_uncached()
        data = self.repo / 'data.txt'
        data.write_text('staged\n')
        self.git('add', 'data.txt')
        self.assert_uncached()
        data.write_text('unstaged\n')
        self.assert_uncached()
        self.git('mv', 'data.txt', 'renamed.txt')
        self.assert_uncached()
        (self.repo / 'renamed.txt').unlink()
        self.assert_uncached()

    def test_git_stat_shortcuts_do_not_hide_same_size_edits(self):
        self.add_tests(a=(0, False))
        self.assert_uncached()
        data = self.repo / 'data.txt'
        saved = data.stat()
        self.git('update-index', '--skip-worktree', 'data.txt')
        data.write_text('two\n')
        os.utime(data, ns=(saved.st_atime_ns, saved.st_mtime_ns))
        self.assert_uncached()

    def test_live_suite_never_reuses_cache(self):
        self.add_tests(a=(0, False))
        self.assert_uncached(BTT_LIVE_TESTS='1')
        self.assert_uncached(BTT_LIVE_TESTS='1')

    def test_failed_forced_run_revokes_previous_pass(self):
        self.add_tests(a=(0, False))
        self.assert_uncached()
        code, output = self.run_suite('--force', FAKE_FAIL='1')
        self.assertEqual(code, 1, output)
        self.assert_uncached()

    def test_cache_expiry_and_future_timestamps(self):
        self.add_tests(a=(0, False))
        self.assert_uncached()
        passes = self.repo / '.git/run_tests/passes.json'
        for value in (time.time() - 7 * 3600, time.time() + 3600, 'bad', float('inf')):
            with self.subTest(value=value):
                records = json.loads(passes.read_text())
                self.assertTrue(records)
                passes.write_text(json.dumps(dict.fromkeys(records, value)))
                self.assert_uncached()

    def test_parallel_failures_stop_queue_and_report_every_failure(self):
        self.add_tests(a=(0.2, True), b=(0.5, True), c=(0, False), d=(0, False))
        code, output = self.run_suite('-j', '2')
        self.assertEqual(code, 1, output)
        self.assertEqual(sorted(self.executions()), ['a', 'b'], output)
        self.assertIn('FAILED: 2 command(s) failed; 2 were not run.', output)
        self.assertIn('output-a', output)
        self.assertIn('output-b', output)

    def test_durations_schedule_slowest_first_and_success_output_is_visible(self):
        self.add_tests(a=(0, False), b=(0, False), c=(0, False))
        state = self.repo / '.git/run_tests'
        state.mkdir()
        (state / 'timings.json').write_text(json.dumps({'tests/test_a.py': 2, 'tests/test_b.py': 5}))
        code, output = self.run_suite('-j', '1')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.executions(), ['c', 'b', 'a'])
        self.assertIn('output-a', output)

    def test_nested_agent_worktrees_are_not_discovered_or_fingerprinted(self):
        self.add_tests(a=(0, False))
        self.assert_uncached()
        nested = self.repo / '.claude/worktrees/other/tests'
        nested.mkdir(parents=True)
        (nested / 'test_broken.py').write_text('raise RuntimeError("not this checkout")')
        code, output = self.run_suite()
        self.assertEqual(code, 0, output)
        self.assertIn('PASS: all 1 suite commands completed (cached', output)
        self.assertEqual(self.executions(), ['a'])

    def test_submodule_working_bytes_invalidate_cache(self):
        source = self.repo.parent / 'source'
        source.mkdir()
        self.git('init', '-q', cwd=source)
        (source / 'data.txt').write_text('one')
        self.git('add', '-A', cwd=source)
        self.git('commit', '-qm', 'source', cwd=source)
        self.git('-c', 'protocol.file.allow=always', 'submodule', 'add', str(source), 'vendor')
        self.add_tests(a=(0, False))
        self.assert_uncached()
        (self.repo / 'vendor/data.txt').write_text('two')
        self.assert_uncached()
        (self.repo / 'vendor/new.txt').write_text('one')
        self.assert_uncached()
        (self.repo / 'vendor/new.txt').write_text('two')
        self.assert_uncached()

    def test_van_compute_untracked_bytes_and_resolver_errors(self):
        checkout = self.repo.parent / 'van-compute'
        checkout.mkdir()
        self.git('init', '-q', cwd=checkout)
        self.git('commit', '--allow-empty', '-qm', 'checkout', cwd=checkout)
        untracked = checkout / 'new.py'
        untracked.write_text('one')
        helper = self.repo / 'pi/tests/dashboard/_van_compute_checkout.py'
        helper.parent.mkdir(parents=True)
        helper.write_text(f'from pathlib import Path\nCHECKOUT = Path({str(checkout)!r})\n')
        self.add_tests(a=(0, False))
        self.assert_uncached()
        untracked.write_text('two')
        self.assert_uncached()
        helper.write_text('raise ImportError("broken resolver")')
        self.assertIn('Pass cache unavailable', self.assert_uncached())
        self.assert_uncached()

    def test_common_lock_directory_works_from_another_cwd_and_worktree(self):
        self.add_tests(a=(0, False))
        code, output = self.run_suite(cwd=self.repo.parent)
        self.assertEqual(code, 0, output)
        worktree = self.repo / '.claude/worktrees/linked'
        self.git('worktree', 'add', '-q', '-b', 'linked', str(worktree))
        self.repo = worktree
        self.assert_uncached(cwd=worktree.parent)
        common = Path(self.git('rev-parse', '--git-common-dir').decode().strip())
        if not common.is_absolute():
            common = worktree / common
        self.assertTrue((common / 'run_tests/passes.json').is_file())
        self.assertFalse((worktree / '.git/run_tests').exists())

    def test_launch_error_is_reported_and_stops_new_commands(self):
        removed = self.repo / 'zzgone'
        removed.mkdir()
        (removed / 'test_b.py').write_text(FAKE_TEST.format(name='b', sleep=0, fail=False))
        self.add_tests(a=(0, False))
        first = self.repo / 'tests/test_a.py'
        first.write_text(first.read_text() + f'\nimport shutil\nshutil.rmtree({str(removed)!r})\n')
        code, output = self.run_suite('-j', '1')
        self.assertEqual(code, 1, output)
        self.assertIn('FAIL zzgone/test_b.py (exit launch error)', output)
        self.assertIn('FAILED: 1 command(s) failed', output)

    def test_help_empty_inventory_and_bad_arguments(self):
        code, output = self.run_suite('--help')
        self.assertEqual(code, 0, output)
        code, output = self.run_suite()
        self.assertEqual(code, 1, output)
        self.assertIn('FAILED: No tests discovered', output)
        for args in (('-j', '0'), ('--timeout', 'nan'), ('--timeout', '-1')):
            self.assertEqual(self.run_suite(*args)[0], 2)

    def test_force_reruns(self):
        self.add_tests(a=(0, False))
        self.run_suite()
        code, output = self.run_suite('--force')
        self.assertEqual(code, 0)
        self.assertNotIn('cached', output)
        self.assertEqual(len(self.executions()), 2)

    def test_failure_exits_one_is_not_cached_and_stops_serial_run(self):
        self.add_tests(a_fail=(0, True), b_ok=(0, False))
        code, output = self.run_suite('-j', '1')
        self.assertEqual(code, 1)
        self.assertIn('FAIL tests/test_a_fail.py (exit 1)', output)
        self.assertIn('FAILED:', output)
        self.assertEqual(self.executions(), ['a_fail'])
        code, _ = self.run_suite('-j', '1')
        self.assertEqual(code, 1)
        # Never cached: the failing test ran again (b_ok may start first now,
        # since it has no recorded duration).
        self.assertEqual(self.executions().count('a_fail'), 2)

    def test_concurrent_runs_execute_suite_once(self):
        self.add_tests(a=(1.5, False), b=(0, False))
        first = self.start()
        time.sleep(0.3)
        second = self.start()
        outputs = [process.communicate(timeout=60)[0] for process in (first, second)]
        self.assertEqual([first.returncode, second.returncode], [0, 0])
        self.assertEqual(sorted(self.executions()), ['a', 'b'])
        self.assertTrue(any('cached' in output for output in outputs))

    def test_edit_during_run_is_not_cached(self):
        self.add_tests(a=(0, False))
        code, output = self.run_suite(FAKE_EDIT=str(self.repo / 'data.txt'))
        self.assertEqual(code, 0)
        self.assertIn('not cached', output)
        code, output = self.run_suite()
        self.assertNotIn('cached', output)
        self.assertEqual(len(self.executions()), 2)


class ProcessTests(RunnerCase):
    def add_process_tree(self):
        self.pid_file = self.repo.parent / 'pids'
        body = '''import os, subprocess, sys, unittest
from pathlib import Path
class T(unittest.TestCase):
    def test_tree(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        Path(os.environ['FAKE_PIDS']).write_text(f'{os.getpid()} {child.pid}')
        child.wait()
'''
        (self.repo / 'tests/test_tree.py').write_text(body)
        self.git('add', '-A')
        self.git('commit', '-qm', 'process tree')

    def assert_children_stopped(self):
        self.assertTrue(self.pid_file.is_file())
        for pid in map(int, self.pid_file.read_text().split()):
            def stopped():
                result = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True)
                return not result.stdout.strip() or result.stdout.strip().startswith(b'Z')
            self.wait_for(stopped)
        with (self.repo / '.git/run_tests/lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_timeout_kills_descendants_and_releases_lock(self):
        self.add_process_tree()
        code, output = self.run_suite('--timeout', '0.5', FAKE_PIDS=str(self.pid_file))
        self.assertEqual(code, 1, output)
        self.assertIn('FAIL tests/test_tree.py (timeout)', output)
        self.assertIn('FAILED:', output)
        self.assert_children_stopped()

    def test_interrupt_and_termination_kill_descendants_and_release_lock(self):
        self.add_process_tree()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig):
                self.pid_file.unlink(missing_ok=True)
                process = self.start(FAKE_PIDS=str(self.pid_file))
                self.wait_for(self.pid_file.exists)
                process.send_signal(sig)
                output, _ = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 128 + sig, output)
                self.assertIn('FAILED: interrupted', output)
                self.assert_children_stopped()
                self.assertEqual(json.loads((self.repo / '.git/run_tests/passes.json').read_text()), {})

    def test_interrupt_can_cancel_lock_wait(self):
        self.add_tests(a=(0, False))
        state = self.repo / '.git/run_tests'
        state.mkdir()
        with (state / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = self.start()
            self.assertIn('Waiting for another', process.stdout.readline())
            process.send_signal(signal.SIGINT)
            output, _ = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 130, output)
        self.assertEqual(self.executions(), [])

    def test_process_leaked_by_a_test_does_not_hold_the_lock(self):
        leaked = Path(self.counter.parent / 'leaked.pid')
        (self.repo / 'tests' / 'test_leak.py').write_text(f"""\
import subprocess, unittest
class T(unittest.TestCase):
    def test_it(self):
        child = subprocess.Popen(['sleep', '30'], start_new_session=True)
        open({str(leaked)!r}, 'w').write(str(child.pid))
""")
        code, output = self.run_suite()
        self.assertEqual(code, 0, output)
        pid = int(leaked.read_text())
        self.addCleanup(subprocess.run, ['kill', '-9', str(pid)], capture_output=True)
        os.kill(pid, 0)  # still running after the suite finished
        with (self.repo / '.git/run_tests/lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

if __name__ == '__main__':
    unittest.main()
