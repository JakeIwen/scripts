"""Single owned watcher and child; mount identity and credentials are never retargeted."""
import base64
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request

from . import binaries, mounts
from .configuration import POLL_SECONDS, STOP_SECONDS, ReceiverStatus, State, config, publish, read


def process_identity(pid=None):
    pid = os.getpid() if pid is None else pid
    try:
        # comm can contain spaces/parentheses; starttime is field 22.
        stat = Path('/proc/{}/stat'.format(pid)).read_text().rsplit(')', 1)[1].split()
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if not boot:
            return None
        return '{}:{}:{}'.format(boot, pid, stat[19])
    except (OSError, IndexError):
        return None


def tailscale_ready(cfg):
    result = subprocess.run([cfg['tailscale'], 'status', '--json'], capture_output=True, timeout=5, check=True)
    value = json.loads(result.stdout)
    if (value.get('BackendState') != 'Running' or value.get('Self', {}).get('Online') is not True
            or cfg['address'] not in value.get('TailscaleIPs', [])):
        raise ValueError('Tailscale is not running at the configured receiver address')


def die_with_parent():
    # DSM is Linux, not systemd. Close the orphan window if the watcher is
    # SIGKILLed; inherit its flock as a second fence until this child exits.
    libc = ctypes.CDLL(None, use_errno=True)
    parent = os.getpid()
    def setup():
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
            os._exit(125)
    return setup


class Server:
    def __init__(self, root, cfg, drive, binary, lock_fd):
        self.child = None
        self.fd = mounts.pin(drive)
        try:
            if mounts.discover() != drive:
                raise ValueError('USB mount changed during server startup')
            # One immutable auth file per child. Never rely on rest-server's
            # delayed htpasswd reload/cache for drive-switch fencing.
            auth = root / 'active.htpasswd'
            digest = base64.b64encode(hashlib.sha1(cfg['transport_password'].encode()).digest()).decode()
            fd = os.open(auth, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as output:
                output.write(drive.marker['repo_id'] + ':{SHA}' + digest + '\n')
                output.flush()
                os.fsync(output.fileno())
            self.child = subprocess.Popen(
                [str(binary), '--listen', '{}:{}'.format(cfg['address'], cfg['port']),
                 '--path', '/proc/self/fd/{}'.format(self.fd), '--htpasswd-file', str(auth), '--append-only'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                pass_fds=(self.fd, lock_fd), preexec_fn=die_with_parent(), start_new_session=True,
                env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
            self.ready(cfg, drive.marker['repo_id'])
        except BaseException:
            self.close()
            raise

    def ready(self, cfg, repo_id):
        request = urllib.request.Request('http://{}:{}/config'.format(cfg['address'], cfg['port']))
        auth = base64.b64encode((repo_id + ':' + cfg['transport_password']).encode()).decode()
        request.add_header('Authorization', 'Basic ' + auth)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        source = os.open('config', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        with os.fdopen(source, 'rb') as stream:
            expected = stream.read(65537)
        if not expected or len(expected) > 65536:
            raise ValueError('Unsafe repository config')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.child.poll() is not None:
                raise ValueError('rest-server exited before accepting connections (check bind/permissions)')
            try:
                with opener.open(request, timeout=0.2) as response:
                    if response.read(65537) != expected:
                        raise ValueError('Receiver config readback does not match pinned repository')
                time.sleep(0.1)
                if self.child.poll() is None:
                    return
            except OSError:
                time.sleep(0.05)
        raise ValueError('rest-server did not listen at the configured address')

    def close(self):
        if self.child is not None:
            if self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=STOP_SECONDS)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait()
            else:
                self.child.wait()
            self.child = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def watch(root):
    flags = os.O_RDWR | os.O_NOFOLLOW
    fd = os.open(root / 'watch.lock', flags)
    child, active = None, None
    identity = process_identity()
    stopping = False
    def stop(signum, frame):
        nonlocal stopping
        stopping = True
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        if identity is None:
            raise ValueError('Linux process identity is unavailable')
        binary = binaries.verify(root)
        while not stopping and read(root / 'enabled.json') == {'enabled': True}:
            state: ReceiverStatus = {'pid': os.getpid(), 'identity': identity, 'updated': time.time(), 'state': State.WAITING}
            try:
                cfg = config(root)
                tailscale_ready(cfg)
                drive = mounts.discover()
                key = (drive.identity, cfg) if drive else None
                if key != active or (child and child.child.poll() is not None):
                    if child:
                        child.close()
                        child = None
                    active = None
                    if drive:
                        child = Server(root, cfg, drive, binary, fd)
                        active = key
                if child:
                    state.update(state=State.SERVING, repo_id=drive.marker['repo_id'],
                                 label=drive.marker['label'], server_pid=child.child.pid,
                                 server_identity=process_identity(child.child.pid))
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                if child:
                    child.close()
                    child = None
                active = None
                # No private config, transport credentials or provider output in status.
                state.update(state=State.ERROR, cause='Receiver validation failed; inspect config, Tailscale, USB mount and binary')
            publish(root / 'status.json', state)
            # Poll deliberate stop quickly even while waiting for a USB drive.
            deadline = time.monotonic() + POLL_SECONDS
            while not stopping and time.monotonic() < deadline and (root / 'enabled.json').exists():
                time.sleep(0.1)
        return 0
    finally:
        if child:
            child.close()
        if read(root / 'status.json') and read(root / 'status.json').get('identity') == identity:
            publish(root / 'status.json', {'state': State.STOPPED, 'updated': time.time()})
        os.close(fd)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
