"""A real tty driver: byte capture and cursor replies, without a screen emulator."""
import errno
import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import termios
import time


LAUNCHER = Path(__file__).resolve().parents[1] / "run"


class PTYDriver:
    def __init__(self, command, environment, cwd, rows=36, columns=110):
        self.master, self.slave = pty.openpty()
        self.original_attributes = termios.tcgetattr(self.slave)
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        self.process = subprocess.Popen(command, stdin=self.slave, stdout=self.slave,
                                        stderr=self.slave, cwd=cwd, env=environment,
                                        start_new_session=True)
        self.output = bytearray()
        self.query_tail = b""

    def send(self, data):
        os.write(self.master, data)

    def pump(self, duration=0.05):
        readable, _, _ = select.select([self.master], [], [], duration)
        if not readable:
            return
        try:
            data = os.read(self.master, 65536)
        except OSError as error:
            if error.errno == errno.EIO:
                return
            raise
        self.output.extend(data)
        pending = self.query_tail + data
        count = pending.count(b"\x1b[6n")
        for _ in range(count):
            self.send(b"\x1b[1;1R")
        # Only incomplete queries can belong to the next chunk.
        self.query_tail = next((pending[-size:] for size in (3, 2, 1)
                                if pending.endswith(b"\x1b[6n"[:size])), b"")

    def until(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump()
            if predicate():
                return
        raise AssertionError("Timed out. TTY tail: {!r}".format(bytes(self.output[-12000:])))

    def pause(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.pump(max(0, min(0.05, deadline - time.monotonic())))

    def submit(self, text):
        self.send(text.encode("utf-8"))
        self.pause(0.25)
        self.send(b"\r")

    def quit_codex(self):
        self.send(b"\x03")
        self.pause(0.2)
        if self.process.poll() is None:
            self.send(b"\x03")
        self.until(lambda: self.process.poll() is not None, timeout=8)

    def close(self):
        try:
            if self.process.poll() is None:
                self.process.terminate()
                deadline = time.monotonic() + 4
                while self.process.poll() is None and time.monotonic() < deadline:
                    self.pump()
                if self.process.poll() is None:
                    self.process.kill()
                self.process.wait(timeout=5)
        finally:
            os.close(self.master)
            os.close(self.slave)
