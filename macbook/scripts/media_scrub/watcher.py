"""The detached watcher: holds one ssh session to the Pi's `serve`, writes snapshot.json
and brokers seeks arriving on ctl.sock. It exits when the menu stops calling `status`."""
import json
import os
import selectors
import signal
import socket
import stat
import subprocess
import sys
import time
import traceback

from . import config
from .broker import SeekBroker
from .records import RecordError, build_status, is_seekable, validate_sample, \
    validate_seek_request, validate_seek_result
from .state_dir import StateDir

LOCK_RETRY_SECONDS = 1.0
CLIENT_REQUEST_SECONDS = 2.0


def ssh_argv():
    ssh = os.environ.get(config.SSH_ENV) or config.DEFAULT_SSH
    return [ssh] + list(config.SSH_OPTIONS) + [config.REMOTE_HOST, config.REMOTE_COMMAND]


class Client:
    def __init__(self, sock):
        self.sock = sock
        self.buffer = b""
        self.deadline = time.monotonic() + CLIENT_REQUEST_SECONDS
        self.open = True

    def send(self, result):
        if not self.open:
            return
        try:
            self.sock.settimeout(0.5)
            self.sock.sendall((json.dumps(result) + "\n").encode("utf-8"))
        except OSError:
            pass
        self.close()

    def close(self):
        self.open = False
        try:
            self.sock.close()
        except OSError:
            pass


class Watcher:
    def __init__(self, state):
        self.state = state
        self.selector = selectors.DefaultSelector()
        self.clients = {}
        self.proc = None
        self.listener = None
        self.stop_reason = None
        self.got_sample = False
        self.bad_lines = 0
        self.buffer = b""
        self.latest = None            # (sample, wall time received, monotonic received)
        self.broker = SeekBroker(self._send_to_pi, lambda client, result: client.send(result),
                                 self._check_request)

    # --- ssh session -------------------------------------------------
    def _send_to_pi(self, message):
        self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def start_ssh(self):
        # stdin stays open: closing it (or our dying) is the Pi's cue to stop `serve`.
        self.proc = subprocess.Popen(ssh_argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=None, close_fds=True)
        os.set_blocking(self.proc.stdout.fileno(), False)
        self.selector.register(self.proc.stdout, selectors.EVENT_READ, "ssh")

    def shutdown_ssh(self):
        proc = self.proc
        if proc is None:
            return
        try:
            proc.stdin.close()
        except OSError:
            pass
        for step in (lambda: None, proc.terminate, proc.kill):
            step()
            try:
                proc.wait(timeout=config.SSH_CLOSE_WAIT_SECONDS)
                break
            except subprocess.TimeoutExpired:
                continue
        proc.wait()
        try:
            proc.stdout.close()
        except OSError:
            pass

    # --- samples -----------------------------------------------------
    def _check_request(self, entry):
        latest = self.latest
        age = None if latest is None else time.monotonic() - latest[2]
        sample = None if latest is None else latest[0]
        if sample is None or not 0 <= age <= config.SEEKABLE_AGE_SECONDS:
            return ("stale", "no fresh sample from the Pi")
        if not is_seekable(sample, age):
            return ("not_seekable", "the player is not seekable")
        if sample["source"] != entry["source"] or sample["media_id"] != entry["media_id"]:
            return ("identity", "what is playing changed")
        return None

    def _handle_line(self, raw):
        try:
            data = json.loads(raw.decode("utf-8"))
            kind = data.get("type") if isinstance(data, dict) else None
            if kind == "sample":
                self._on_sample(validate_sample(data))
            elif kind == "seek_result":
                self.broker.on_result(validate_seek_result(data), time.monotonic())
            else:
                raise RecordError("unknown line type")
        except (ValueError, RecordError):
            self.bad_lines += 1
            print("ignored bad line from the Pi", file=sys.stderr, flush=True)
            if self.bad_lines >= config.MAX_BAD_LINES:
                self.stop_reason = "too many malformed lines"

    def _on_sample(self, sample):
        self.latest = (sample, time.time(), time.monotonic())
        if not self.got_sample:
            self.got_sample = True
            self.state.clear_failures()
        self._write_snapshot(error=None)

    def _write_snapshot(self, error):
        sample, received = (self.latest[0], self.latest[1]) if self.latest else (None, None)
        self.state.write_json(self.state.snapshot, {
            "sample": sample, "received_at": received, "error": error,
            "seek_ack": self.broker.on_sample() if error is None else self.broker.ack,
            "broker": self.broker.state(), "pid": os.getpid()})

    def _read_ssh(self):
        try:
            chunk = os.read(self.proc.stdout.fileno(), 65536)
        except (BlockingIOError, InterruptedError):
            return
        if chunk == b"":
            self.stop_reason = "ssh exited"
            return
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            self._handle_line(line)
        if len(self.buffer) > config.MAX_LINE_BYTES:
            self.stop_reason = "oversized line from the Pi"

    # --- control socket ----------------------------------------------
    def bind_socket(self):
        self.state.check_socket_path()
        try:
            os.unlink(self.state.socket)          # a stale file from a dead watcher
        except FileNotFoundError:
            pass
        old = os.umask(0o177)
        try:
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(self.state.socket)
        finally:
            os.umask(old)
        os.chmod(self.state.socket, 0o600)
        info = os.lstat(self.state.socket)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("ctl.sock is not a private socket")
        listener.listen(8)
        listener.setblocking(False)
        self.listener = listener
        self.selector.register(listener, selectors.EVENT_READ, "listener")

    def _accept(self):
        try:
            sock, _ = self.listener.accept()
        except (BlockingIOError, InterruptedError):
            return
        sock.setblocking(False)
        client = Client(sock)
        self.clients[sock] = client
        self.selector.register(sock, selectors.EVENT_READ, "client")

    def _drop_client(self, client):
        try:
            self.selector.unregister(client.sock)
        except (KeyError, ValueError, OSError):
            pass
        self.clients.pop(client.sock, None)

    def _read_client(self, client):
        try:
            chunk = client.sock.recv(8192)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            chunk = b""
        if chunk:
            client.buffer += chunk
        if b"\n" not in client.buffer and chunk and len(client.buffer) <= 8192:
            return
        # Complete, closed or oversized: this client gets exactly one verdict.
        self._drop_client(client)
        line = client.buffer.split(b"\n", 1)[0]
        try:
            if len(client.buffer) > 8192:
                raise RecordError("request too large")
            request = validate_seek_request(json.loads(line.decode("utf-8")))
        except (ValueError, RecordError) as error:
            client.send({"ok": False, "superseded": False, "reason": "invalid",
                         "message": "bad request: %s" % error})
            return
        try:
            self.broker.submit(client, request, time.monotonic())
        except OSError:
            self.stop_reason = "ssh write failed"

    def _expire_clients(self):
        now = time.monotonic()
        for client in list(self.clients.values()):
            if now >= client.deadline and client.open:
                self._drop_client(client)
                client.send({"ok": False, "superseded": False, "reason": "invalid",
                             "message": "request not received in time"})

    # --- main loop ---------------------------------------------------
    def _events(self):
        for key, _ in self.selector.select(0.2):
            if key.data == "ssh":
                self._read_ssh()
            elif key.data == "listener":
                self._accept()
            else:
                client = self.clients.get(key.fileobj)
                if client is not None:
                    self._read_client(client)

    def loop(self):
        started = time.monotonic()
        idle = config.idle_seconds()
        while self.stop_reason is None:
            self._events()
            if self.stop_reason is not None:
                break
            self._expire_clients()
            try:
                self.broker.expire(time.monotonic())
            except OSError:
                self.stop_reason = "ssh write failed"
            if self.state.heartbeat_age() > idle:
                self.stop_reason = "idle"
            elif time.monotonic() - started >= config.WATCHER_MAX_SECONDS:
                self.stop_reason = "max runtime"
            elif self.proc.poll() is not None and self.stop_reason is None:
                self._events()                       # drain what the dead ssh left
                self.stop_reason = self.stop_reason or "ssh exited"

    def finish(self):
        reason = self.stop_reason or "error"
        self.broker.fail_all("watcher stopping")
        for client in list(self.clients.values()):
            client.close()
        if self.listener is not None:
            self.listener.close()
            try:
                os.unlink(self.state.socket)
            except FileNotFoundError:
                pass
        self.shutdown_ssh()
        failed = reason not in ("idle", "max runtime", "signal")
        if failed:
            message = "ssh session ended (%s)" % reason
            if self.proc is not None and self.proc.returncode not in (None, 0):
                message += ", exit status %s" % self.proc.returncode
            try:
                self._write_snapshot(error=message)
                if not self.got_sample:
                    self.state.record_failure(message, time.time())
            except OSError:
                pass


def acquire_watch_lock(state):
    import fcntl
    descriptor = os.open(state.watch_lock, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + LOCK_RETRY_SECONDS
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(descriptor)
                return None
            time.sleep(0.05)


def run_watcher():
    state = StateDir.open()
    lock = acquire_watch_lock(state)
    if lock is None:
        return 0                                     # another watcher owns the session
    watcher = Watcher(state)

    def stop(signum, frame):
        watcher.stop_reason = watcher.stop_reason or "signal"
    for name in ("SIGTERM", "SIGHUP", "SIGINT"):
        signal.signal(getattr(signal, name), stop)
    try:
        state.write_json(state.watch_info, {"pid": os.getpid(), "started_at": time.time()})
        watcher.bind_socket()
        watcher.start_ssh()
        watcher.loop()
    except Exception:
        traceback.print_exc()
        watcher.stop_reason = watcher.stop_reason or "start failed: " + sys.exc_info()[0].__name__
    finally:
        watcher.finish()
    return 0
