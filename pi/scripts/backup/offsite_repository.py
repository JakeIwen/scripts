"""Bounded subprocess supervision and local repository trust boundary."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import resource
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import time

from backup_locks import LockClass
from offsite_config import GUARD_SECONDS, LABEL_PATTERN, MARKER, REPO_ID_PATTERN
from offsite_config import PROXY_VARIABLES, REPO_PATH, SCHEMA, STOP_SECONDS, MarkerRecord, RepositoryRecords
from offsite_config import validate_marker
from time_machine_store import atomic_json

# The Pi's registry of known repositories (label -> MarkerRecord), root-only.
REPOSITORIES = 'repos.json'


class Interrupted(RuntimeError):
    pass


# offsite_mounts.sh release exit codes (OFFSITE.md "Locks").
RELEASED, RELEASE_BUSY, ALREADY_UNMOUNTED = 0, 3, 4
REDACTED = '[redacted]'
# Bound cooperative stop latency without a filesystem probe for each repository object.
SHARE_GUARD_ENTRIES = 128


def no_core_dumps():
    # restic and this process's own commands hold the repository key and the DSM password; a core
    # file would leave them on disk. As a preexec the child's rclone inherits it; call it directly
    # before this process reads a credential.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


class CommandFailed(RuntimeError):
    """A fixed command exited with an unaccepted status.

    The message is redacted; `output` is the command's raw text, for a caller that
    only classifies it (offsite_remote_init) and never prints it.
    """

    def __init__(self, message, output):
        super().__init__(message)
        self.output = output


def secret_forms(secret):
    """The secret as printed raw and inside JSON strings (Go's encoder also escapes <, > and &)."""
    escaped = json.dumps(secret, ensure_ascii=False)[1:-1]
    go = escaped.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return {secret, escaped, go, json.dumps(secret)[1:-1]}


def redacted(text, hidden):
    """Diagnostic text with every form of each secret replaced; never applied to text that is parsed."""
    for form in sorted({form for secret in hidden if secret for form in secret_forms(secret)}, key=len, reverse=True):
        text = text.replace(form, REDACTED)
    return text


class Commands:
    def __init__(self, cfg, locks):
        # locks: the admitted offsite_locks.OffsiteLocks whose descriptors children inherit.
        self.cfg, self.locks = cfg, locks
        self.cancelled = None  # the signal's name, once TERM/INT arrives
        self.stopped = None  # the latched stop cause; see stop_cause()
        self.remote_control = None  # admitted remote backup's label and immutable revision

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
        """Why the run must stop (TERM/INT or the ignition flag), or None; once seen, for good."""
        # Latched: on_stop may already have closed the run's holds, so an
        # ignition flag that clears again must not let later work run without them.
        if self.stopped is None:
            if self.cancelled:
                self.stopped = f'Stopped by {self.cancelled}; rerun when parked to resume'
            elif self.cfg.ignition.exists() or self.cfg.ignition.is_symlink():
                self.stopped = 'Ignition is on; rerun when parked to resume'
            elif self.remote_control is not None:
                from offsite_control import stop_cause
                self.stopped = stop_cause(self.cfg, self.remote_control)
        return self.stopped

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

    def execute(self, name, *args, accept, timeout=60, input_text=None, line=None, guard=None, cleanup=False,
                rest_auth=None, rclone_env=None, redact=(), holds=True, on_stop=None):
        """Run one fixed command; returns (exit code, output).

        The output and every `line` are the command's raw text, so parsed JSON is
        never altered; only this method's error text is redacted. rest_auth's
        secret and every string in redact are replaced there, and callers that
        print output use the repository's redact() (plan review R3).
        rclone_env (restic only, never with rest_auth) carries the shared-folder
        transport's RCLONE_* values; afterwards any rclone server restic left
        behind is stopped. holds=False passes only the job descriptor; on_stop
        runs once if a stop arrives while a cleanup command (which ignores stops) still runs.
        """
        if not cleanup:
            self.allowed()
        argv = [self.cfg.commands[name], *map(str, args)]
        environment = self._environment(name, holds, rest_auth, rclone_env)
        hidden = (*redact, *(rest_auth[1:] if rest_auth else ()))
        with subprocess.Popen(argv, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment,
                              pass_fds=self.locks.fds(resources=holds), start_new_session=True,
                              preexec_fn=no_core_dumps if name == 'restic' else None) as child:
            try:
                if input_text is not None:
                    child.stdin.write(input_text.encode())
                    child.stdin.close()
                output = self._read(child, timeout, line, guard, cleanup, on_stop)
                try:
                    rc = child.wait(timeout=STOP_SECONDS)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(f'{name} closed output but failed to exit') from exc
                if rc not in accept:
                    raise CommandFailed(f'{name} exited {rc}: {redacted(output, hidden)[-3000:].strip()}', output)
                return rc, output
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=STOP_SECONDS)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()
                if rclone_env is not None:
                    # restic is gone; the run's snapshot (and its rclone) is removed only after this.
                    from offsite_smb import reap
                    reap(self.cfg.commands['rclone'])

    def _environment(self, name, holds, rest_auth, rclone_env):
        # Every held job/disk/data/slot descriptor is inherited (holds=False:
        # only the job's), so an orphaned child (restic above all) keeps the
        # run's holds visible to the drain.
        environment = self.locks.child_environment({**os.environ, 'LC_ALL': 'C'}, resources=holds)
        if rclone_env is not None:
            if (name != 'restic' or rest_auth is not None or not rclone_env
                    or any(not key.startswith('RCLONE_') for key in rclone_env)):
                raise ValueError('An rclone environment is only allowed for restic, without REST authentication')
            from offsite_smb import without_ambient
            environment = {**without_ambient(environment), **rclone_env}
        if name == 'restic':
            environment = {key: value for key, value in environment.items() if not key.startswith('RESTIC_')}
            environment['RESTIC_PROGRESS_FPS'] = '0.2'
        if rest_auth is not None:
            if name != 'restic' or len(rest_auth) != 2:
                raise ValueError('REST authentication is only allowed for restic')
            environment.update(RESTIC_REST_USERNAME=rest_auth[0], RESTIC_REST_PASSWORD=rest_auth[1])
            environment = {key: value for key, value in environment.items() if key.lower() not in PROXY_VARIABLES}
        return environment

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
    path = cfg.state / REPOSITORIES
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


def register(cfg, known: RepositoryRecords, marker: MarkerRecord):
    """Add a new label's marker to the records read as `known`, keeping every other entry.

    Callers hold job-offsite (prepare-disk also the exclusive legacy lock), so no
    other writer can change repos.json between their read and this write.
    """
    validate_marker(marker, marker.get('label'))
    if marker['label'] in known['repos']:
        raise RuntimeError(f"{marker['label']} is already a known repository; nothing registered")
    known['repos'][marker['label']] = marker
    durable_json(cfg.state / REPOSITORIES, known)


def block_device_number(path):
    """The kernel device number, not the filesystem holding its /dev node."""
    info = os.stat(path)
    if not stat.S_ISBLK(info.st_mode):
        raise RuntimeError('Verified offsite source is not a block device')
    return info.st_rdev


class LocalRepository:
    transport_kind = None  # a local disk has no network transport

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

    def redact(self, text):
        """Text fit to print or record; a local repository passes no secret to restic."""
        return text

    def share_for_smb(self):
        """Add cross-uid access inside the verified repository, never to its marker/mount.

        DSM's non-root SMB uid differs from Pi root. Descriptor-relative, no-follow
        opens keep a replaced entry from redirecting chmod outside the repository;
        fchmod also avoids Linux's unsupported chmod(follow_symlinks=False).
        """
        if getattr(self, 'remote', False):
            raise RuntimeError('Only local repositories can be shared for SMB')
        self.commands.allowed()
        expected_device = self.exact_mount()
        if self.path.resolve() != self.path:
            raise RuntimeError('Unsafe repository directory for SMB sharing')
        rejected = {'symlinks': 0, 'filesystem boundaries': 0, 'special files': 0, 'hard links': 0}
        visited = 0

        def share(parent, name):
            nonlocal visited
            if visited % SHARE_GUARD_ENTRIES == 0 or self.commands.cancelled:
                self.commands.allowed()
            visited += 1
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                rejected['symlinks'] += 1
                return
            if info.st_dev != device:
                rejected['filesystem boundaries'] += 1
                return
            directory = stat.S_ISDIR(info.st_mode)
            if not directory and not stat.S_ISREG(info.st_mode):
                rejected['special files'] += 1
                return
            # A file hard-linked elsewhere would change permissions outside this tree.
            if not directory and info.st_nlink != 1:
                rejected['hard links'] += 1
                return
            wanted = 0o777 if directory else 0o666
            if not directory and stat.S_IMODE(info.st_mode) & wanted == wanted:
                return
            flags = os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else os.O_NONBLOCK)
            fd = os.open(name, flags, dir_fd=parent)
            try:
                current = os.fstat(fd)
                if (current.st_dev, current.st_ino, current.st_mode, current.st_nlink) != (
                        info.st_dev, info.st_ino, info.st_mode, info.st_nlink):
                    raise RuntimeError('Repository entry changed during SMB sharing')
                if directory:
                    with os.scandir(fd) as entries:
                        for entry in entries:
                            share(fd, entry.name)
                mode = stat.S_IMODE(os.fstat(fd).st_mode)
                if mode | wanted != mode:
                    os.fchmod(fd, mode | wanted)
            finally:
                os.close(fd)

        # Pin the verified mount too, so opening the root cannot follow a replaced parent.
        mount_fd = os.open(self.mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            device = os.fstat(mount_fd).st_dev
            if block_device_number(expected_device) != device:
                raise RuntimeError('Offsite mount changed before SMB sharing')
            root = os.stat(REPO_PATH, dir_fd=mount_fd, follow_symlinks=False)
            if not stat.S_ISDIR(root.st_mode) or root.st_dev != device:
                raise RuntimeError('Unsafe repository directory for SMB sharing')
            share(mount_fd, REPO_PATH)
        finally:
            os.close(mount_fd)
        self.commands.allowed()
        if any(rejected.values()):
            detail = ', '.join(f'{count} {kind}' for kind, count in rejected.items() if count)
            raise RuntimeError(f'Repository SMB sharing refused {detail}')

    def check(self, subset=None):
        owned, verified = [], False
        self.hold_target()
        try:
            self.commands.allowed()
            self.mount_source(self.label, on_mount=owned.append)
            self.verify()
            verified = True
            options = ['--read-data-subset', subset] if subset else []
            output = self.restic('check', *options, timeout=None, guard=self.exact_mount)
            try:
                self.share_for_smb()
            except Exception:
                # Passing restic evidence survives a stop or refusal during the permission walk.
                print(output, end='', flush=True)
                raise
            return output
        finally:
            try:
                if owned or (verified and self.commands.cancelled):
                    note = self.unmount()
                    if note:
                        print(note, file=sys.stderr, flush=True)
            finally:
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
