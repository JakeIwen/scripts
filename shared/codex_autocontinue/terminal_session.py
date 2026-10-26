"""TTY ownership, title stack and signal forwarding for the transparent proxy."""
import errno
import fcntl
import os
import signal
import termios
import time
import tty


def write_bytes(fd, data):
    while data:
        written = os.write(fd, data)
        data = data[written:]


def window_size(fd):
    return fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)


def set_window_size(fd, size):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, size)


def signal_child(pid, signum):
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass
    except PermissionError:
        # On macOS the process group can disappear just before its leader is reaped.
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            pass


class TerminalSession:
    def __init__(self, pid, master, attributes, input_fd=0, output_fd=1):
        self.pid = pid
        self.master = master
        self.attributes = attributes
        self.input_fd = input_fd
        self.output_fd = output_fd
        self.previous_handlers = {}
        self.title = ""
        self.termination = None
        self.suspend_requested = False

    def __enter__(self):
        try:
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT,
                           signal.SIGUSR1, signal.SIGUSR2, signal.SIGALRM,
                           signal.SIGWINCH, signal.SIGTSTP, signal.SIGCONT):
                self.previous_handlers[signum] = signal.signal(signum, self._signal)
            tty.setraw(self.input_fd, termios.TCSANOW)
            self.resize()
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *_):
        try:
            termios.tcsetattr(self.input_fd, termios.TCSANOW, self.attributes)
        finally:
            try:
                self.set_title("")
            except OSError:
                pass
            for signum, previous in self.previous_handlers.items():
                signal.signal(signum, previous)

    def resize(self):
        try:
            set_window_size(self.master, window_size(self.input_fd))
        except OSError as error:
            if error.errno not in (errno.EIO, errno.EBADF, errno.ENOTTY):
                raise

    def _signal(self, signum, _frame):
        if signum == signal.SIGWINCH:
            self.resize()
        elif signum == signal.SIGTSTP:
            self.suspend_requested = True
        elif signum == signal.SIGCONT:
            self.resize()
        else:
            self.termination = signum
            signal_child(self.pid, signum)

    def suspend(self):
        self.suspend_requested = False
        # The child owns an orphaned PTY process group, where default SIGTSTP is ignored.
        signal_child(self.pid, signal.SIGSTOP)
        title = self.title
        self.set_title("")
        termios.tcsetattr(self.input_fd, termios.TCSANOW, self.attributes)
        os.kill(os.getpid(), signal.SIGSTOP)
        tty.setraw(self.input_fd, termios.TCSANOW)
        self.resize()
        signal_child(self.pid, signal.SIGCONT)
        self.set_title(title)

    def set_title(self, title):
        if title == self.title:
            return
        if title:
            if not self.title:
                write_bytes(self.output_fd, b"\x1b[22;0t")
            write_bytes(self.output_fd, ("\x1b]0;" + title + "\x07").encode("ascii"))
        else:
            # xterm/iTerm's title stack restores the title without querying the real tty.
            write_bytes(self.output_fd, b"\x1b[23;0t")
        self.title = title


def reap_child(pid, status, termination=None):
    """Bound cleanup even if the child ignores a termination signal."""
    if status is not None:
        return status
    waited, status = os.waitpid(pid, os.WNOHANG)
    if waited:
        return status
    signal_child(pid, signal.SIGKILL if termination else signal.SIGHUP)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        waited, status = os.waitpid(pid, os.WNOHANG)
        if waited:
            return status
        time.sleep(0.02)
    signal_child(pid, signal.SIGKILL)
    return os.waitpid(pid, 0)[1]
