"""Black-box tests of run_tests.py: signals, timeouts and descendant processes."""
from pathlib import Path
import fcntl
import json
import os
import signal
import subprocess
import unittest

from run_tests_fixture import RunnerCase


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
        # The timeout must outlast the fixture's Python startup (it writes the pid file), or a loaded
        # machine kills it first; the child sleeps 30 s, so 5 s still proves the timeout path.
        code, output = self.run_suite('--timeout', '5',FAKE_PIDS=str(self.pid_file))
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
                output, _ = process.communicate(timeout=60)
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
            output, _ = process.communicate(timeout=60)
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

    def test_command_leaving_only_a_zombie_in_its_group_passes(self):
        # macOS killpg fails with EPERM when the group holds only zombies, as it
        # does when a test's background child exits just as its command ends.
        parent = Path(self.counter.parent / 'zombie-parent.pid')
        (self.repo / 'tests' / 'test_zombie.py').write_text(f"""\
import os, subprocess, time, unittest
PARENT = {str(parent)!r}
def state(pid):
    return subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)],
                          capture_output=True, text=True).stdout.strip()
class T(unittest.TestCase):
    def test_it(self):
        if os.fork() == 0:
            grandchild = os.fork()
            if grandchild == 0:
                os._exit(0)  # stays in the group as a zombie nobody reaps
            os.setsid()
            deadline = time.monotonic() + 10
            while not state(grandchild).startswith('Z') and time.monotonic() < deadline:
                time.sleep(0.02)
            if not state(grandchild).startswith('Z'):
                os._exit(1)
            with open(PARENT + '.tmp', 'w') as handle:
                handle.write(str(os.getpid()))
            os.replace(PARENT + '.tmp', PARENT)
            time.sleep(30)
            os._exit(0)
        deadline = time.monotonic() + 10
        while not os.path.exists(PARENT):
            self.assertLess(time.monotonic(), deadline, 'grandchild never became a zombie')
            time.sleep(0.02)
""")
        def kill_parent():
            if parent.exists():
                subprocess.run(['kill', '-9', parent.read_text()], capture_output=True)
        self.addCleanup(kill_parent)
        code, output = self.run_suite()
        self.assertEqual(code, 0, output)
        self.assertIn('PASS: all', output)

if __name__ == '__main__':
    unittest.main()
