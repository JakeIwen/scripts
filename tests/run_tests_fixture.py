"""Shared fixture for the black-box run_tests.py tests (test_run_tests*.py)."""
from pathlib import Path
import os
import shutil
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
               if not key.startswith('GIT_') and key not in
               ('PYTHONPATH', 'OFFSITE_RESTIC', 'OFFSITE_REST_SERVER')}
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

    def wait_for(self, predicate, timeout=60):
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
