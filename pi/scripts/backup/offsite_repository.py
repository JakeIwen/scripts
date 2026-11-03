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
import time

import backup_locks
from offsite_config import GUARD_SECONDS, LABEL_PATTERN, MARKER, REPO_ID_PATTERN
from offsite_config import REPO_PATH, SCHEMA, STOP_SECONDS, RepositoryRecords
from time_machine_store import atomic_json


class Interrupted(RuntimeError):
    pass


class Commands:
    def __init__(self, cfg):
        self.cfg = cfg
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

    def allowed(self):
        if self.cancelled:
            raise Interrupted(f'Stopped by {self.cancelled}; rerun when parked to resume')
        if self.cfg.ignition.exists() or self.cfg.ignition.is_symlink():
            raise Interrupted('Ignition is on; rerun when parked to resume')

    def run(self, name, *args, timeout=60, input_text=None, line=None,
            guard=None, cleanup=False):
        if not cleanup:
            self.allowed()
        argv = [self.cfg.commands[name], *map(str, args)]
        environment = {**os.environ, 'LC_ALL': 'C'}
        if name == 'restic':
            environment = {key: value for key, value in environment.items() if not key.startswith('RESTIC_')}
            environment['RESTIC_PROGRESS_FPS'] = '0.2'
        with subprocess.Popen(argv, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment,
                              pass_fds=(self.cfg.lock_fd,), start_new_session=True) as child:
            try:
                if input_text is not None:
                    child.stdin.write(input_text.encode())
                    child.stdin.close()
                output = self._read(child, timeout, line, guard, cleanup)
                try:
                    rc = child.wait(timeout=STOP_SECONDS)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(f'{name} closed output but failed to exit') from exc
                if rc:
                    raise RuntimeError(f'{name} exited {rc}: {output[-3000:].strip()}')
                return output
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=STOP_SECONDS)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()

    def _read(self, child, timeout, line, guard, cleanup):
        started = time.monotonic()
        next_guard = started
        buffer, output = b'', ''
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                now = time.monotonic()
                if not cleanup:
                    self.allowed()
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


def admit(cfg):
    if os.geteuid() != cfg.owner:
        raise RuntimeError('Run offsite_backup.sh as root')
    backup_locks.validate_lock_file(backup_locks.legacy_lock())
    backup_locks.require_held(cfg.lock_fd, backup_locks.legacy_lock())


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


def validate_marker(marker, label):
    if (not isinstance(marker, dict) or type(marker.get('schema')) is not int or marker['schema'] != SCHEMA or
            marker.get('label') != label or not re.fullmatch(LABEL_PATTERN, label) or
            marker.get('repo_path') != REPO_PATH or
            not re.fullmatch(REPO_ID_PATTERN, str(marker.get('repo_id', ''))) or
            type(marker.get('priority')) is not int or not 0 <= marker['priority'] <= 2147483647 or
            not isinstance(marker.get('created'), str) or not marker['created']):
        raise RuntimeError('Invalid offsite marker')


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

    def unmount(self):
        self.exact_mount(cleanup=True)
        self.commands.run('umount', '--', self.mount, cleanup=True)

    def restic(self, *args, **kwargs):
        return self.commands.run('restic', '--repo', self.path, '--password-file',
                                 self.cfg.password, '--no-cache', *args, **kwargs)

    def check(self, subset=None):
        owned, verified = [], False
        try:
            self.mount_source(self.label, on_mount=owned.append)
            self.verify()
            verified = True
            options = ['--read-data-subset', subset] if subset else []
            return self.restic('check', *options, timeout=None, guard=self.exact_mount)
        finally:
            if owned or (verified and self.commands.cancelled):
                self.unmount()

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
