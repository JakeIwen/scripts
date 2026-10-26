import json
import os
import pty
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import unittest
import fcntl
from pathlib import Path

from shared.codex_autocontinue.terminal_session import TerminalSession
from shared.codex_autocontinue.tests.pty_driver import LAUNCHER, PTYDriver


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.environ = dict(os.environ, JACOBR_CONFIG_DIR=str(self.directory))
        self.values = self.directory / "codex-autocontinue.json"
        self.values.write_text(json.dumps({"enabled": True, "base_seconds": 1, "jitter": 0}))

    def driver(self, code):
        driver = PTYDriver([str(LAUNCHER), "--", sys.executable, "-c", code],
                           self.environ, self.directory)
        self.addCleanup(driver.close)
        return driver

    def test_non_tty_exec_preserves_argv_cwd_stdin_output_and_exit(self):
        code = "import os,sys; print(os.getcwd()); print(sys.argv[1:]); print(sys.stdin.read()); sys.exit(37)"
        result = subprocess.run([str(LAUNCHER), "--", sys.executable, "-c", code, "a b", "--x"],
                                input=b"hello", capture_output=True,
                                cwd=self.directory, env=self.environ)
        self.assertEqual(result.returncode, 37)
        self.assertEqual(result.stdout.decode().splitlines(),
                         [str(self.directory.resolve()), "['a b', '--x']", "hello"])
        self.assertEqual(result.stderr, b"")

    def test_proxy_preserves_binary_bytes_and_child_exit_status(self):
        code = "import os,tty; tty.setraw(0); os.write(1,b'READY'); data=os.read(0,4); os.write(1,data); os._exit(37)"
        driver = self.driver(code)
        driver.until(lambda: b"READY" in driver.output)
        driver.send(b"\x03\x00\r\xff")
        driver.until(lambda: driver.process.poll() is not None)
        self.assertEqual(bytes(driver.output), b"READY\x03\x00\r\xff")
        self.assertEqual(driver.process.returncode, 37)

    def test_signal_is_forwarded_and_handler_exit_status_is_preserved(self):
        code = '''import os,signal,time
def finish(*_):
    time.sleep(0.2)
    os.write(1,b'FINAL')
    os._exit(23)
signal.signal(signal.SIGTERM,finish)
os.write(1,b'READY')
time.sleep(60)
'''
        driver = self.driver(code)
        driver.until(lambda: b"READY" in driver.output)
        driver.process.send_signal(signal.SIGTERM)
        driver.until(lambda: driver.process.poll() is not None)
        self.assertEqual(driver.process.returncode, 23, bytes(driver.output))
        self.assertIn(b"FINAL", driver.output)

    def test_closed_tty_does_not_kill_child_before_its_real_exit(self):
        code = ("import os,time; os.close(0); os.close(1); os.close(2); "
                "time.sleep(0.2); os._exit(37)")
        driver = self.driver(code)
        driver.until(lambda: driver.process.poll() is not None)
        self.assertEqual(driver.process.returncode, 37)

    def test_signal_exit_convention(self):
        driver = self.driver("import os,signal; os.kill(os.getpid(),signal.SIGTERM)")
        driver.until(lambda: driver.process.poll() is not None)
        self.assertEqual(driver.process.returncode, 128 + signal.SIGTERM)

    def test_initial_window_size_and_sigwinch_propagate(self):
        code = r'''import os,fcntl,termios,struct,signal,tty
size=lambda: os.write(1,repr(struct.unpack('HHHH',fcntl.ioctl(0,termios.TIOCGWINSZ,b'0'*8))[:2]).encode())
signal.signal(signal.SIGWINCH,lambda *_:size())
tty.setraw(0)
size()
os.read(0,1)
'''
        driver = self.driver(code)
        driver.until(lambda: b"(36, 110)" in driver.output)
        fcntl.ioctl(driver.slave, termios.TIOCSWINSZ, struct.pack("HHHH", 44, 90, 0, 0))
        driver.process.send_signal(signal.SIGWINCH)
        driver.until(lambda: b"(44, 90)" in driver.output)
        driver.send(b"q")
        driver.until(lambda: driver.process.poll() is not None)
        self.assertEqual(driver.process.returncode, 0)

    def test_suspend_restores_the_tty_and_resume_keeps_forwarding(self):
        driver = self.driver("import os,tty; tty.setraw(0); os.write(1,b'READY'); os.write(1,os.read(0,1))")
        driver.until(lambda: b"READY" in driver.output)
        driver.process.send_signal(signal.SIGTSTP)

        def stopped():
            pid, status = os.waitpid(driver.process.pid, os.WNOHANG | os.WUNTRACED)
            return pid and os.WIFSTOPPED(status)

        driver.until(stopped)
        self.assertTrue(termios.tcgetattr(driver.slave)[3] & termios.ICANON)
        driver.process.send_signal(signal.SIGCONT)
        driver.pause(0.1)
        driver.send(b"x")
        driver.until(lambda: driver.process.poll() is not None)
        self.assertEqual(driver.process.returncode, 0)
        self.assertEqual(bytes(driver.output), b"READYx")

    def test_tty_flags_restore_on_exception(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        attributes = termios.tcgetattr(slave)
        with open(os.devnull, "wb") as output:
            terminal = TerminalSession(os.getpid(), master, attributes, slave, output.fileno())
            with self.assertRaisesRegex(RuntimeError, "fixture"):
                with terminal:
                    self.assertFalse(termios.tcgetattr(slave)[3] & termios.ICANON)
                    terminal.set_title("codex: retry 1/5")
                    raise RuntimeError("fixture")
        restored = termios.tcgetattr(slave)
        # macOS sets the kernel-managed pending-input bit when canonical mode returns.
        restored[3] &= ~getattr(termios, "PENDIN", 0)
        attributes[3] &= ~getattr(termios, "PENDIN", 0)
        self.assertEqual(restored, attributes)
        self.assertEqual(terminal.title, "")

    def test_keystroke_cancels_pending_retry_without_changing_input(self):
        code = r'''import os,tty,select,time
tty.setraw(0)
os.write(1,"■ Selected model is at capacity. Please try a different model.\r\n".encode())
data=b''
end=time.monotonic()+1.8
while time.monotonic()<end:
    if select.select([0],[],[],0.05)[0]: data+=os.read(0,100)
os.write(1,b'INPUT:'+data.hex().encode())
'''
        driver = self.driver(code)
        driver.until(lambda: b"codex: retry" in driver.output)
        driver.send(b"x")
        driver.until(lambda: driver.process.poll() is not None)
        self.assertIn(b"INPUT:78", driver.output)
        self.assertNotIn(b"INPUT:78636f6e74696e7565", driver.output)
        self.assertIn(b"\x1b[23;0t", driver.output)

    def test_live_settings_disable_a_pending_retry(self):
        code = r'''import os,tty,select
tty.setraw(0)
os.write(1,"■ Selected model is at capacity.\r\n".encode())
data=os.read(0,100) if select.select([0],[],[],1.8)[0] else b''
os.write(1,b'INPUT:'+repr(data).encode())
'''
        driver = self.driver(code)
        driver.until(lambda: b"codex: retry" in driver.output)
        self.values.write_text(json.dumps({"enabled": False}))
        driver.until(lambda: driver.process.poll() is not None)
        self.assertIn(b"INPUT:b''", driver.output)
        self.assertIn(b"\x1b[23;0t", driver.output)
