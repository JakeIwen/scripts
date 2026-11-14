"""Black-box tests of run_tests.py: pass-cache invalidation."""
from pathlib import Path
import os
import unittest

from run_tests_fixture import RunnerCase


class CacheTests(RunnerCase):
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

    def test_offsite_opt_ins_and_external_binary_contents_invalidate_cache(self):
        (self.repo / 'tests/test_offsite.py').write_text('''\
import os, subprocess, unittest
class T(unittest.TestCase):
    def test_it(self):
        with open(os.environ['FAKE_COUNTER'], 'a') as log:
            log.write('offsite\\n')
        for key in ('OFFSITE_RESTIC', 'OFFSITE_REST_SERVER'):
            if not os.environ.get(key):
                self.skipTest('offsite binary not enabled')
        for key in ('OFFSITE_RESTIC', 'OFFSITE_REST_SERVER'):
            result = subprocess.check_output([os.environ[key]], text=True)
            self.assertEqual(result.strip(), 'good')
''')
        binaries = {}
        for key in ('OFFSITE_RESTIC', 'OFFSITE_REST_SERVER'):
            path = self.repo.parent / key.lower()
            path.write_text('#!/bin/sh\nprintf good\n')
            path.chmod(0o755)
            binaries[key] = str(path)
        self.assertIn('skipped=1', self.assert_uncached())
        self.assertIn('(cached:', self.run_suite()[1])
        self.assertIn('skipped=1', self.assert_uncached(OFFSITE_RESTIC=binaries['OFFSITE_RESTIC']))
        self.assertNotIn('skipped=', self.assert_uncached(**binaries))
        self.assertIn('(cached:', self.run_suite(**binaries)[1])
        for key, filename in binaries.items():
            with self.subTest(binary=key):
                path = Path(filename)
                saved, content = path.stat(), path.read_text()
                path.write_text(content.replace('good', 'fail'))
                os.utime(path, ns=(saved.st_atime_ns, saved.st_mtime_ns))
                self.assertEqual(path.stat().st_size, saved.st_size)
                code, output = self.run_suite(**binaries)
                self.assertEqual(code, 1, output)
                self.assertNotIn('(cached:', output)
                path.write_text(content)
                os.utime(path, ns=(saved.st_atime_ns, saved.st_mtime_ns))
                self.assert_uncached(**binaries)
                self.assertIn('(cached:', self.run_suite(**binaries)[1])


if __name__ == '__main__':
    unittest.main()
