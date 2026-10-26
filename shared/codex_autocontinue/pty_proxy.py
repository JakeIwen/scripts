"""Transparent Unix PTY forwarding with cancellable capacity-error retries."""
import errno
import os
import pty
import random
import select
import termios
import time

from shared.python.tool_settings import Reloader
from .detector import CapacityDetector, SubmissionDetector
from .retry_state import RetryState
from .terminal_session import (TerminalSession, reap_child, set_window_size,
                               window_size, write_bytes)


class PTYProxy:
    def __init__(self, terminal, settings, rng):
        self.terminal = terminal
        self.settings = settings
        self.rng = rng
        self.detector = CapacityDetector()
        self.submissions = SubmissionDetector()
        self.retry = RetryState()
        self.status = None

    def _child_output(self):
        try:
            data = os.read(self.terminal.master, 65536)
        except OSError as error:
            if error.errno != errno.EIO:
                raise
            return False
        if not data:
            return False
        write_bytes(self.terminal.output_fd, data)
        events = self.detector.feed(data)
        if events.completed_turns:
            self.retry.completed_turn()
        if events.capacity_errors:
            self.retry.capacity_error(time.monotonic(), self.settings.poll().values, self.rng)
        return True

    def _user_input(self):
        data = os.read(self.terminal.input_fd, 65536)
        if not data:
            return False
        self.retry.cancel()
        self.detector.user_activity()
        if (self.submissions.feed(data) and not self.detector.alternate_screen
                and not self.submissions.submitted_capacity_quote):
            self.detector.new_turn()
        write_bytes(self.terminal.master, data)
        return True

    def _automatic_input(self):
        values = self.settings.poll().values
        now = time.monotonic()
        data = self.retry.tick(now, values, self.detector.alternate_screen)
        if data:
            write_bytes(self.terminal.master, data)
            if data == b"\r":
                self.detector.new_turn()
        if self.retry.bell_pending:
            write_bytes(self.terminal.output_fd, b"\a")
            self.retry.bell_pending = False
        self.terminal.set_title(self.retry.title(now, values))

    def _poll_child(self):
        if self.status is not None:
            return
        pid, status = os.waitpid(self.terminal.pid, os.WNOHANG | os.WUNTRACED)
        if pid and os.WIFSTOPPED(status):
            self.terminal.suspend_requested = True
        elif pid:
            self.status = status

    def run(self):
        output_open = True
        terminating_since = None
        while True:
            if self.terminal.termination:
                self.retry.cancel()
                if terminating_since is None:
                    terminating_since = time.monotonic()
                elif time.monotonic() - terminating_since >= 2:
                    break
            self._poll_child()
            if self.terminal.suspend_requested:
                self.retry.cancel()
                self.terminal.suspend()
            descriptors = [self.terminal.input_fd, self.terminal.master] if output_open else []
            readable, _, _ = select.select(descriptors, [], [], 0.05)
            # Takeover wins over a retry whose deadline fell in the same select cycle.
            if self.terminal.input_fd in readable and not self._user_input():
                break
            if self.terminal.master in readable and not self._child_output():
                output_open = False
                self.retry.cancel()
            if self.status is not None:
                if not output_open or not readable:
                    break
            elif output_open and not self.terminal.termination:
                self._automatic_input()
        return self.status


def run(command):
    """Exec noninteractive commands directly; proxy only when stdin is a tty."""
    if not os.isatty(0):
        os.execvp(command[0], command)
    settings = Reloader("codex-autocontinue")
    attributes = termios.tcgetattr(0)
    size = window_size(0)
    pid, master = pty.fork()
    if pid == 0:
        try:
            termios.tcsetattr(0, termios.TCSANOW, attributes)
            set_window_size(0, size)
            os.execvp(command[0], command)
        except OSError as error:
            os.write(2, (str(error) + "\r\n").encode())
            os._exit(127 if error.errno == errno.ENOENT else 126)
    proxy = PTYProxy(TerminalSession(pid, master, attributes), settings, random.Random())
    try:
        with proxy.terminal:
            proxy.run()
    finally:
        try:
            status = reap_child(pid, proxy.status, proxy.terminal.termination)
        finally:
            os.close(master)
    return os.waitstatus_to_exitcode(status)
