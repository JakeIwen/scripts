"""Black-box tests of run_tests.py: state files, discovery, fingerprints."""
import fcntl
import json
import os
import time
import unittest

from run_tests_fixture import FAKE_TEST, RunnerCase


class StateTests(RunnerCase):
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
        self.assertRegex(output, r'FAILED: 2 command\(s\) failed \(tests/test_\w+\.py, tests/test_\w+\.py\); 2 were not run\.')
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


if __name__ == '__main__':
    unittest.main()
