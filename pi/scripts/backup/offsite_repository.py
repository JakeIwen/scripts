"""Bounded subprocess supervision and local repository trust boundary."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import time

from backup_locks import LockClass
from offsite_config import GUARD_SECONDS, LABEL_PATTERN, MARKER, REPO_ID_PATTERN
from offsite_config import REPO_PATH, SCHEMA, STOP_SECONDS, RepositoryRecords, validate_marker
from time_machine_store import atomic_json


class Interrupted(RuntimeError):
    pass


# offsite_mounts.sh release exit codes (OFFSITE.md "Locks").
RELEASED, RELEASE_BUSY, ALREADY_UNMOUNTED = 0, 3, 4


class Commands:
    def __init__(self, cfg, locks):
        # locks: the admitted offsite_locks.OffsiteLocks whose descriptors children inherit.
        self.cfg, self.locks = cfg, locks
        self.cancelled = None

    @contextmanager
    def signals(self):
        def stop(signum, frame):
            self.cancelled = signal.Signals(signum).name
        previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            yield
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def stop_cause(self):
        """Why the run must stop (TERM/INT or the ignition flag), or None."""
        if self.cancelled:
            return f'Stopped by {self.cancelled}; rerun when parked to resume'
        if self.cfg.ignition.exists() or self.cfg.ignition.is_symlink():
            return 'Ignition is on; rerun when parked to resume'
        return None

    def allowed(self):
        cause = self.stop_cause()
        if cause:
            raise Interrupted(cause)

    def run(self, name, *args, **kwargs):
        """Run one fixed command and return its output; any nonzero exit raises."""
        return self.execute(name, *args, accept=(0,), **kwargs)[1]

    def status(self, name, *args, accept, **kwargs):
        """Return the exit code of a command whose accepted nonzero codes carry meaning."""
        return self.execute(name, *args, accept=accept, **kwargs)[0]

    def execute(self, name, *args, accept, timeout=60, input_text=None, line=None,
                guard=None, cleanup=False, rest_auth=None, holds=True, on_stop=None):
        """holds=False passes only the job descriptor; on_stop runs once if a
        stop arrives while a cleanup command (which ignores stops) still runs."""
        if not cleanup:
            self.allowed()
        argv = [self.cfg.commands[name], *map(str, args)]
        # Every held job/disk/data/slot descriptor is inherited (holds=False:
        # only the job's), so an orphaned child (restic above all) keeps the
        # run's holds visible to the drain.
        environment = self.locks.child_environment({**os.environ, 'LC_ALL': 'C'}, resources=holds)
        if name == 'restic':
            environment = {key: value for key, value in environment.items() if not key.startswith('RESTIC_')}
            environment['RESTIC_PROGRESS_FPS'] = '0.2'
        if rest_auth is not None:
            if name != 'restic' or len(rest_auth) != 2:
                raise ValueError('REST authentication is only allowed for restic')
            environment.update(RESTIC_REST_USERNAME=rest_auth[0], RESTIC_REST_PASSWORD=rest_auth[1])
            environment = {key: value for key, value in environment.items()
                           if key.lower() not in ('http_proxy', 'https_proxy', 'all_proxy')}
        def safe_line(text):
            return text.replace(rest_auth[1], '[redacted]') if rest_auth else text
        original_line = line
        if line:
            line = lambda text: original_line(safe_line(text))
        with subprocess.Popen(argv, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment,
                              pass_fds=self.locks.fds(resources=holds), start_new_session=True) as child:
            try:
                if input_text is not None:
                    child.stdin.write(input_text.encode())
                    child.stdin.close()
                output = safe_line(self._read(child, timeout, line, guard, cleanup, on_stop))
                try:
                    rc = child.wait(timeout=STOP_SECONDS)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(f'{name} closed output but failed to exit') from exc
                if rc not in accept:
                    raise RuntimeError(f'{name} exited {rc}: {output[-3000:].strip()}')
                return rc, output
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=STOP_SECONDS)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()

    def _read(self, child, timeout, line, guard, cleanup, on_stop):
        started = time.monotonic()
        next_guard = started
        buffer, output = b'', ''
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                now = time.monotonic()
                if not cleanup:
                    self.allowed()
                elif on_stop is not None and self.stop_cause():
                    on_stop()
                    on_stop = None
                if timeout is not None and now - started > timeout:
                    raise RuntimeError('command timed out')
                if guard and now >= next_guard:
                    guard()
                    next_guard = time.monotonic() + GUARD_SECONDS
                for key, _ in selector.select(0.25):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        break
                    buffer += chunk
                    if len(buffer) > 16 * 1024 * 1024:
                        raise RuntimeError('command output exceeded bounded line size')
                    while b'\n' in buffer:
                        raw, buffer = buffer.split(b'\n', 1)
                        text = raw.decode('utf-8', errors='replace')
                        if line:
                            line(text)
                        output += text + '\n'
                        if line:
                            output = output[-65536:]
                        elif len(output) > 16 * 1024 * 1024:
                            raise RuntimeError('command output exceeded discovery limit')
        if buffer:
            text = buffer.decode('utf-8', errors='replace')
            if line:
                line(text)
            output += text
        return output


def private_directory(path, owner):
    if path.is_symlink():
        raise RuntimeError(f'Unsafe private directory: {path}')
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.stat()
    if info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError(f'Private directory must be owner-only mode 0700: {path}')


def read_record(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise RuntimeError(f'Missing or unsafe record: {path}')
    return json.loads(path.read_text())


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_json(path, value):
    atomic_json(path, value)
    sync_directory(path.parent)


def password(cfg, create=False):
    path = cfg.password
    if create:
        private_directory(path.parent, cfg.owner)
        if not path.exists() and not path.is_symlink():
            # Exclusive creation never replaces the one password shared by all drives.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(secrets.token_urlsafe(48) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            sync_directory(path.parent)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != cfg.owner or
            stat.S_IMODE(info.st_mode) != 0o600 or info.st_size < 32):
        raise RuntimeError('Offsite password must be a root-owned mode-0600 regular file')


def records(cfg, optional=False) -> RepositoryRecords:
    path = cfg.state / 'repos.json'
    if optional and not path.exists() and not path.is_symlink():
        return {'schema': SCHEMA, 'repos': {}}
    info = path.lstat()
    if info.st_uid != cfg.owner or stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError('Known repositories must be owner-only')
    value = read_record(path)
    if (not isinstance(value, dict) or type(value.get('schema')) is not int or
            value['schema'] != SCHEMA or not isinstance(value.get('repos'), dict)):
        raise RuntimeError('Invalid known repository record')
    for label, marker in value['repos'].items():
        validate_marker(marker, label)
    return value


class LocalRepository:
    def __init__(self, cfg, commands, label):
        if not re.fullmatch(LABEL_PATTERN, label):
            raise ValueError('Label must be 1–16 ASCII letters/digits/dot/underscore/hyphen')
        self.cfg, self.commands, self.label = cfg, commands, label
        self.mount = cfg.mount_root / label
        self.path = self.mount / REPO_PATH

    def exact_mount(self, label=None, *, cleanup=False):
        label = label or self.label
        target = self.cfg.mount_root / label
        if target.is_symlink() or target.resolve() != target:
            raise RuntimeError(f'Unsafe mount path: {target}')
        device = self.commands.run('mounts', 'resolve', label, cleanup=cleanup).strip()
        if not device.startswith('/') or '\n' in device:
            raise RuntimeError(f'Ambiguous device for {label}')
        source = self.commands.run('findmnt', '-rn', '-M', target, '-o', 'SOURCE', cleanup=cleanup).strip()
        if source != device:
            raise RuntimeError(f'{target} is not mounted from exact label {label}: {source}')
        targets = self.commands.run('findmnt', '-rn', '-S', device, '-o', 'TARGET', cleanup=cleanup).strip()
        if targets != str(target):
            raise RuntimeError(f'{label} has unexpected or duplicate mounts')
        return device

    def mount_source(self, label, on_mount=None):
        # A full successful findmnt inventory distinguishes absence from probe failure.
        rows = json.loads(self.commands.run('findmnt', '--json', '--list', '-o', 'TARGET,SOURCE'))
        entries = rows.get('filesystems')
        if not isinstance(entries, list) or not entries:
            raise RuntimeError('Cannot establish mount inventory')
        target = self.cfg.mount_root / label
        existing = any(row.get('target') == str(target) for row in entries)
        if not existing:
            if on_mount:
                on_mount(label)
            self.commands.run('mounts', 'mount', label)
        self.exact_mount(label)
        return not existing

    def hold_target(self):
        """Shared target-disk lock, taken before mounting; the ignition drain waits on it."""
        self.commands.locks.take(LockClass.DISK, self.label, exclusive=False)

    def release(self, label):
        """Unmount a disk this run mounted unless another job still holds it.

        The run's own descriptor must already be closed. Returns a note for the
        two expected non-errors; anything else raises as a cleanup error.
        The helper inherits only the job descriptor, and a stop arriving while it
        runs closes every resource descriptor at once: a stalled unmount must not
        keep the run's other disks (the target above all) past a drain's wait.
        """
        rc = self.commands.status('mounts', 'release', label, cleanup=True, holds=False,
                                  on_stop=self.commands.locks.close_resources,
                                  accept=(RELEASED, RELEASE_BUSY, ALREADY_UNMOUNTED))
        return {RELEASED: None, RELEASE_BUSY: f'{label}: left mounted; another job holds its disk lock',
                ALREADY_UNMOUNTED: f'{label}: already unmounted (by a drain or another job)'}[rc]

    def unmount(self):
        self.commands.locks.close(LockClass.DISK, self.label)
        return self.release(self.label)

    def restic(self, *args, **kwargs):
        return self.commands.run('restic', '--repo', self.path, '--password-file',
                                 self.cfg.password, '--no-cache', *args, **kwargs)

    def check(self, subset=None):
        owned, verified = [], False
        self.hold_target()
        try:
            self.commands.allowed()
            self.mount_source(self.label, on_mount=owned.append)
            self.verify()
            verified = True
            options = ['--read-data-subset', subset] if subset else []
            return self.restic('check', *options, timeout=None, guard=self.exact_mount)
        finally:
            if owned or (verified and self.commands.cancelled):
                note = self.unmount()
                if note:
                    print(note, file=sys.stderr, flush=True)
            self.commands.locks.close(LockClass.DISK, self.label)

    def verify(self):
        self.exact_mount()
        password(self.cfg)
        marker = read_record(self.mount / MARKER)
        validate_marker(marker, self.label)
        known = records(self.cfg)['repos'].get(self.label)
        if known != marker:
            raise RuntimeError('Offsite marker/repository is not recorded on this Pi')
        if self.path.is_symlink() or self.path.resolve() != self.path or not self.path.is_dir():
            raise RuntimeError('Unsafe repository directory')
        actual = json.loads(self.restic('cat', 'config')).get('id')
        if actual != marker['repo_id']:
            raise RuntimeError('Unknown repository ID: restic config does not match the recorded marker')
        return marker
