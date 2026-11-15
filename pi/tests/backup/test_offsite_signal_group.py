"""signal_group tolerates a command group that just exited, never a live one."""

import signal
import subprocess
import unittest
from unittest import mock

import offsite_repository


class FakeChild:
    pid = 4242

    def __init__(self, exit_code):
        self.exit_code = exit_code

    def wait(self, timeout=None):
        if self.exit_code is None:
            raise subprocess.TimeoutExpired('fake', timeout)
        return self.exit_code


class SignalGroupTests(unittest.TestCase):
    def test_exited_child_is_tolerated_for_both_errors(self):
        for error in (PermissionError(1, 'Operation not permitted'), ProcessLookupError(3, 'No such process')):
            with self.subTest(error=type(error).__name__), mock.patch('os.killpg', side_effect=error):
                offsite_repository.signal_group(FakeChild(0), signal.SIGTERM)

    def test_live_child_that_cannot_be_signalled_raises(self):
        for error in (PermissionError(1, 'Operation not permitted'), ProcessLookupError(3, 'No such process')):
            with self.subTest(error=type(error).__name__), mock.patch('os.killpg', side_effect=error):
                with self.assertRaises(type(error)):
                    offsite_repository.signal_group(FakeChild(None), signal.SIGTERM)

    def test_signal_is_sent_to_the_process_group(self):
        with mock.patch('os.killpg') as killpg:
            offsite_repository.signal_group(FakeChild(None), signal.SIGKILL)
        killpg.assert_called_once_with(4242, signal.SIGKILL)


if __name__ == '__main__':
    unittest.main()
