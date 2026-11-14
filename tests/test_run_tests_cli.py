"""Black-box tests of run_tests.py: locking, arguments, selection, scheduling."""
from pathlib import Path
import time
import unittest

from run_tests_fixture import FAKE_TEST, RunnerCase


class CliTests(RunnerCase):
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
        self.assertIn('FAIL zzgone/test_b.py (launch error)', output)
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

    def add_other_test(self, name):
        directory = self.repo / 'other'
        directory.mkdir(exist_ok=True)
        (directory / f'test_{name}.py').write_text(FAKE_TEST.format(name=name, sleep=0, fail=False))
        self.git('add', '-A')
        self.git('commit', '-qm', 'other tests')

    def test_path_selection_runs_subset_and_never_certifies_whole_suite(self):
        self.add_tests(a=(0, False))
        self.add_other_test('b')
        code, output = self.run_suite('--path', 'tests')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.executions(), ['a'])
        self.assertIn('PASS: all 1 suite commands under tests completed.', output)
        # The whole suite has its own key, so the subset's pass doesn't cover it.
        code, output = self.run_suite()
        self.assertEqual(code, 0, output)
        self.assertIn('PASS: all 2 suite commands completed.', output)
        self.assertEqual(sorted(self.executions()), ['a', 'a', 'b'])
        code, output = self.run_suite('--path', 'tests/')
        self.assertEqual(code, 0, output)
        self.assertIn('PASS: all 1 suite commands under tests/ completed (cached', output)
        code, output = self.run_suite('--exclude-path', 'other')
        self.assertEqual(code, 0, output)
        self.assertIn('Discovered 1 test files excluding other;', output)
        self.assertEqual(self.executions().count('b'), 1)

    def test_path_selection_rejects_unknown_and_outside_paths(self):
        self.add_tests(a=(0, False))
        for args, message in ((('--path', 'nope'), '--path nope matches no repository path'),
                              (('--exclude-path', 'tests/x'), 'matches no repository path'),
                              (('--path', '../repo'), 'must be a path inside the repository'),
                              (('--path', '.'), 'must be a path inside the repository'),
                              (('--path', str(self.repo)), 'must be a path inside the repository')):
            with self.subTest(args=args):
                code, output = self.run_suite(*args)
                self.assertEqual(code, 1, output)
                self.assertIn(message, output)
        code, output = self.run_suite('--path', 'data.txt')
        self.assertEqual(code, 1, output)
        self.assertIn('No tests discovered', output)
        self.assertEqual(self.executions(), [])

    def test_edit_during_run_is_not_cached(self):
        self.add_tests(a=(0, False))
        code, output = self.run_suite(FAKE_EDIT=str(self.repo / 'data.txt'))
        self.assertEqual(code, 0)
        self.assertIn('not cached', output)
        code, output = self.run_suite()
        self.assertNotIn('cached', output)
        self.assertEqual(len(self.executions()), 2)


if __name__ == '__main__':
    unittest.main()
