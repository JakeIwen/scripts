#!/usr/bin/env python3
"""Immutable, encrypted Borg recovery generations uploaded directly to iCloud.

The shell entrypoint holds the existing backup lock on fd 9. Children inherit
it so ignition's emergency abort can find the entire operation. The uploader
never creates/prunes Borg archives in a copied repository.
"""
import configparser
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import termios
import time
import uuid

from icloud_uplink import decide

CONFIG = Path('/etc/vanpi-icloud-backup.json')
STATE_DIR = Path('/var/lib/vanpi-icloud-backup')
RCLONE = '/usr/local/bin/rclone'
GENERATION = re.compile(r'vanpi-\d{8}T\d{6}Z-[0-9a-f]{8}\Z')
OWNER = 'vanpi-icloud-weekly-v1'
CHILD = None
CHILD_INTERACTIVE = False


class Deferred(Exception):
    pass


class AuthenticationRequired(RuntimeError):
    """A safe, actionable description of a rejected saved Apple session."""


def command_failure(program, operation, returncode, stderr):
    """Classify known failures without logging server bodies or credentials."""
    base = Path(program).name
    text = stderr.casefold()
    if base == 'rclone':
        if any(message in text for message in (
                'trust token expired', 'missing icloud trust token',
                'invalid session token')):
            return AuthenticationRequired(
                'iCloud rejected the saved session and requires fresh 2FA; '
                'run icloud_backup.sh --login')
        if 'missing pcs cookies' in text or 'requestpcs:' in text:
            return AuthenticationRequired(
                'iCloud web-data access needs approval on a trusted Apple device; '
                'run icloud_backup.sh --login')
        if 'authsrpcomplete' in text or 'incorrect username or password' in text:
            return AuthenticationRequired(
                'Apple rejected the account sign-in before 2FA; check the regular '
                'Apple password privately with icloud_backup.sh --login')
    safe_operation = operation if operation in {
        'config', 'copy', 'copyto', 'cat', 'deletefile', 'lsjson', 'check', 'purge',
        'with-lock'} else 'command'
    return RuntimeError(f'{base} {safe_operation} failed (exit {returncode})')


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, sort_keys=True, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path, default=None):
    if not path.exists() and default is not None:
        return default
    with path.open() as f:
        return json.load(f)


def load_config(path=CONFIG):
    cfg = read_json(path)
    if not re.fullmatch(r'icloud:[A-Za-z0-9_-]+/vanpi/weekly', cfg['remote']):
        raise ValueError('remote must be a dedicated icloud:<folder>/vanpi/weekly prefix')
    for k, low, high in [('interval_days', 1, 31), ('keep_generations', 2, 52),
                         ('max_source_age_hours', 1, 168), ('guard_interval_seconds', 1, 10),
                         ('minimum_free_gib', 1, 1000)]:
        if type(cfg[k]) is not int or not low <= cfg[k] <= high:
            raise ValueError('invalid setting: ' + k)
    if not re.fullmatch(r'[1-9][0-9]*[kM]', cfg['bandwidth_limit']):
        raise ValueError('invalid bandwidth_limit')
    if not cfg['blocked_ssids'] or not all(isinstance(x, str) and x for x in cfg['blocked_ssids']):
        raise ValueError('invalid blocked_ssids')
    return cfg


def stop_child():
    global CHILD
    if CHILD is None or CHILD.poll() is not None:
        return
    if CHILD_INTERACTIVE:
        CHILD.terminate()
    else:
        os.killpg(CHILD.pid, signal.SIGCONT)
        os.killpg(CHILD.pid, signal.SIGTERM)
    try:
        CHILD.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if CHILD_INTERACTIVE:
            CHILD.kill()
        else:
            os.killpg(CHILD.pid, signal.SIGKILL)
        CHILD.wait(timeout=5)


def abort(signum, frame):
    stop_child()
    raise Deferred('stopped for disk lifecycle or service shutdown')


def capture(args, timeout=20):
    result = subprocess.run(args, check=True, capture_output=True, text=True,
                            timeout=timeout)
    return result.stdout


def guard(cfg):
    helper = Path(__file__).with_name('icloud_uplink.py')
    try:
        evidence = json.loads(capture(['/usr/sbin/runuser', '-u', 'pi', '--',
                                       '/usr/bin/python3', str(helper)], timeout=35))
        allowed, reason = decide(evidence, cfg['blocked_ssids'])
    except (OSError, ValueError, subprocess.SubprocessError):
        allowed, reason, evidence = False, 'cannot verify current uplinks', {}
    if not allowed:
        raise Deferred(reason)
    selected = sorted((m['name'], m['percent']) for m in evidence['router']['route_members'] if m['percent'] > 0)
    identities = [(name, evidence['ubnt']['ssid'] if name == 'wan' else evidence['wireless_uplinks'][name]['ssid']) for name, weight in selected]
    # Restart connections on ANY path/association change. Existing conntrack
    # flows can stay on the former uplink after mwan3 changes its default.
    signature = json.dumps([evidence['local_route']['dev'], evidence['local_route']['gateway'], selected, identities], sort_keys=True)
    return evidence['local_route']['prefsrc'], signature


def check_parked():
    if Path(os.environ['VANPI_ICLOUD_IGNITION_FLAG']).exists():
        raise Deferred('ignition is on')
    policy = capture(['/home/pi/scripts/policyctl', 'read']).strip()
    if not re.fullmatch(r'1 [01] [01]', policy):
        raise Deferred('HDD policy is off or unavailable')


def run(args, cfg, network=False, interactive=False, parked=True):
    """Guard every network command, including retries, checks and retention."""
    global CHILD, CHILD_INTERACTIVE
    program = args[0]
    operation = args[1] if len(args) > 1 else 'command'
    if parked:
        check_parked()
    proof = guard(cfg) if network else None
    source = proof[0] if network else None
    env = os.environ.copy()
    if network:
        # Do not allow proxy or environment settings to bypass route inspection.
        for k in list(env):
            if k.lower().endswith('_proxy') or k.startswith('RCLONE_'):
                del env[k]
        args = [args[0], '--config', cfg['rclone_config'], '--bind', source,
                '--bwlimit', cfg['bandwidth_limit'], '--transfers', '2',
                '--checkers', '2', '--retries', '1', '--low-level-retries', '2',
                '--contimeout', '20s', '--timeout', '60s', '--stats', '0'] + args[1:]
    inherited = ()
    try:
        os.fstat(9)
        inherited = (9,)
    except OSError:
        pass
    terminal = termios.tcgetattr(sys.stdin.fileno()) if interactive and sys.stdin.isatty() else None
    with tempfile.TemporaryFile(mode='w+t') as output, tempfile.TemporaryFile(mode='w+t') as errors:
        CHILD_INTERACTIVE = interactive
        CHILD = subprocess.Popen(args, stdin=None if interactive else subprocess.DEVNULL,
                                 stdout=None if interactive else output,
                                 stderr=None if interactive else errors, env=env,
                                 start_new_session=not interactive, pass_fds=inherited)
        next_check = time.monotonic() + cfg['guard_interval_seconds']
        try:
            while CHILD.poll() is None:
                time.sleep(0.25)
                if time.monotonic() < next_check:
                    continue
                if parked:
                    check_parked()
                if network:
                    # Pause while collecting fresh evidence: a slow/failed probe
                    # must not leave the upload running throughout its timeout.
                    try:
                        os.kill(CHILD.pid, signal.SIGSTOP)
                        if guard(cfg) != proof:
                            raise Deferred('uplink or association changed during transfer')
                    finally:
                        if CHILD.poll() is None:
                            os.kill(CHILD.pid, signal.SIGCONT)
                next_check = time.monotonic() + cfg['guard_interval_seconds']
            rc = CHILD.returncode
            output.seek(0)
            data = output.read()
            if rc:
                errors.seek(0)
                raise command_failure(program, operation, rc, errors.read(65536))
            return data
        finally:
            if CHILD.poll() is None:
                if interactive:
                    CHILD.terminate()
                    try:
                        CHILD.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        CHILD.kill()
                        CHILD.wait()
                else:
                    stop_child()
            CHILD = None
            if terminal is not None:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, terminal)


def exact_mount(mount, label):
    out = capture(['/usr/bin/findmnt', '-rn', '-M', str(mount), '-o', 'SOURCE']).strip()
    if not out or '\n' in out or mount.is_symlink():
        raise RuntimeError('ambiguous or unsafe backup mount')
    device = Path('/dev/disk/by-label') / label
    if Path(out).resolve(strict=True) != device.resolve(strict=True) or not device.is_block_device():
        raise RuntimeError('backup filesystem label does not match its mount')


def safe_generation(root, name):
    if not GENERATION.fullmatch(name):
        raise ValueError('unsafe generation name')
    path = root / name
    if root.is_symlink() or path.is_symlink() or path.parent.resolve() != root.resolve():
        raise ValueError('unsafe generation path')
    return path


def credentials_ready(cfg):
    p = Path(cfg['rclone_config'])
    if not p.is_file() or p.is_symlink():
        return False
    s = p.stat()
    if s.st_uid != 0 or s.st_mode & 0o077:
        raise RuntimeError('iCloud credentials must be root-owned mode 0600')
    c = configparser.ConfigParser(interpolation=None)
    c.read(p)
    return (c.has_section('icloud') and c['icloud'].get('type') == 'iclouddrive'
            and c['icloud'].get('service', 'drive') == 'drive'
            and bool(c['icloud'].get('trust_token'))
            and bool(c['icloud'].get('cookies')))


def file_manifest(root):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            if (Path(directory) / name).is_symlink():
                raise RuntimeError('repository copy contains a symlink')
        for name in sorted(files):
            p = Path(directory) / name
            if not p.is_file():
                raise RuntimeError('repository copy contains a non-file')
            h = hashlib.sha256()
            with p.open('rb') as f:
                for data in iter(lambda: f.read(4 * 1024 * 1024), b''):
                    h.update(data)
            result[str(p.relative_to(root))] = {'bytes': p.stat().st_size, 'sha256': h.hexdigest()}
    return result


def make_snapshot(cfg, root, repo, name):
    generation = safe_generation(root, name)
    generation.mkdir(mode=0o700, exist_ok=True)
    payload = generation / 'payload'
    payload.mkdir(mode=0o700, exist_ok=True)
    dest = payload / 'repository'
    dest.mkdir(mode=0o700, exist_ok=True)
    if any(p.is_symlink() for p in (generation, payload, dest)):
        raise RuntimeError('unsafe staging subdirectory')
    # Deletion is limited to this unfinished, private staging repository;
    # it has never been uploaded until its manifest is atomically published.
    run(['/usr/bin/borg', 'with-lock', str(repo), '/usr/bin/rsync', '-a', '--delete',
         '--exclude=/lock.exclusive', '--exclude=/lock.roster', str(repo) + '/', str(dest) + '/'], cfg)
    run(['/usr/bin/borg', 'check', '--verify-data', str(dest)], cfg)
    files = file_manifest(dest)
    if 'config' not in files or not any(k.startswith('data/') for k in files):
        raise RuntimeError('copied repository is incomplete')
    manifest = {'owner': OWNER, 'generation': name, 'created_at': time.time(),
                'borg_version': capture(['/usr/bin/borg', '--version']).strip(),
                'files': files}
    atomic_json(payload / 'manifest.json', manifest)
    shutil.copyfile(Path(__file__).with_name('ICLOUD_RESTORE.txt'), payload / 'RESTORE.txt')
    return name


def choose_prunable(names, current, keep):
    """Only our well-formed, verified older generations can be retired."""
    good = sorted({n for n in names if GENERATION.fullmatch(n) and n <= current})
    return good[:-keep] if len(good) > keep else []


def retention(cfg, root, current):
    rows = json.loads(run([RCLONE, 'lsjson', cfg['remote'], '--dirs-only'], cfg, network=True))
    complete = []
    for row in rows:
        name = row.get('Name', '')
        if not row.get('IsDir') or not GENERATION.fullmatch(name) or name > current:
            continue
        entries = json.loads(run([RCLONE, 'lsjson', cfg['remote'] + '/' + name,
                                  '--max-depth', '1'], cfg, network=True))
        if not any(e.get('Name') == '_COMPLETE.json' for e in entries):
            continue
        marker = json.loads(run([RCLONE, 'cat', cfg['remote'] + '/' + name + '/_COMPLETE.json'], cfg, network=True))
        if marker.get('owner') == OWNER and marker.get('generation') == name and marker.get('verification') == 'download-compared':
            complete.append(name)
    for name in choose_prunable(complete, current, cfg['keep_generations']):
        run([RCLONE, 'purge', cfg['remote'] + '/' + name], cfg, network=True)
        local = safe_generation(root, name)
        if local.exists():
            exact_mount(Path(os.environ['VANPI_ICLOUD_BACKUP_MNT']), os.environ['VANPI_ICLOUD_BACKUP_LABEL'])
            marker = read_json(local / '_COMPLETE.json')
            if marker.get('owner') != OWNER or marker.get('generation') != name:
                raise RuntimeError('local retention marker mismatch')
            shutil.rmtree(local)
        print('Retired verified weekly generation ' + name, flush=True)


def notify(state, title, message):
    if time.time() - state.get('last_notification_at', 0) < 86400:
        return
    try:
        subprocess.run(['/home/pi/scripts/ntfy_send.sh', title, message, 'default', 'cloud'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return
    state['last_notification_at'] = time.time()


def weekly(cfg, state):
    if time.time() - state.get('last_success_at', 0) < cfg['interval_days'] * 86400:
        return 'not due'
    if not credentials_ready(cfg) or not (STATE_DIR / 'authenticated.json').is_file():
        raise Deferred('iCloud login is required; run icloud_backup.sh --login')
    if (STATE_DIR / 'authentication-error.json').is_file():
        raise Deferred('iCloud session needs renewal; run icloud_backup.sh --login')
    check_parked()
    guard(cfg)
    stamp = Path(os.environ['VANPI_ICLOUD_BORG_STAMP'])
    if not stamp.is_file() or not 0 <= time.time() - stamp.stat().st_mtime <= cfg['max_source_age_hours'] * 3600:
        raise Deferred('local Borg backup is missing or stale')
    mount = Path(os.environ['VANPI_ICLOUD_BACKUP_MNT'])
    label = os.environ['VANPI_ICLOUD_BACKUP_LABEL']
    probe = subprocess.run(['/usr/bin/findmnt', '-rn', '-M', str(mount), '-o', 'SOURCE'], capture_output=True, text=True)
    mounted_here = False
    try:
        if probe.returncode == 1 and not probe.stdout.strip() and not probe.stderr.strip():
            run(['/home/pi/scripts/mount_disks.sh', label], cfg)
            mounted_here = True
        elif probe.returncode:
            raise RuntimeError('cannot determine backup mount state')
        exact_mount(mount, label)
        root = mount / 'icloud-weekly'
        if root.is_symlink():
            raise RuntimeError('unsafe staging root')
        root.mkdir(mode=0o700, exist_ok=True)
        if root.stat().st_uid != 0 or root.stat().st_mode & 0o077:
            raise RuntimeError('staging must be root-owned and private')
        repo = Path(os.environ['BORG_REPO'])
        if repo.is_symlink() or mount.resolve() not in repo.resolve().parents:
            raise RuntimeError('repository is outside the backup disk')
        meta = configparser.ConfigParser(interpolation=None)
        meta.read(repo / 'config')
        if not meta.has_option('repository', 'key'):
            raise RuntimeError('expected a repokey-encrypted Borg repository')
        pending = state.get('pending')
        if pending:
            old = safe_generation(root, pending)
            manifest = read_json(old / 'payload' / 'manifest.json', {})
            if manifest and time.time() - manifest['created_at'] > cfg['interval_days'] * 86400:
                pending = None  # Preserve the old partial; publish a fresh recovery point.
        if not pending:
            pending = 'vanpi-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
            state['pending'] = pending
            atomic_json(STATE_DIR / 'state.json', state)
        generation = safe_generation(root, pending)
        if not (generation / 'payload' / 'manifest.json').is_file():
            size = sum((Path(directory)/name).stat().st_size for directory, dirs, files in os.walk(repo) for name in files)
            if shutil.disk_usage(root).free < size + cfg['minimum_free_gib'] * 1024**3:
                raise Deferred('insufficient staging disk headroom')
            state['phase'] = 'preparing'
            atomic_json(STATE_DIR / 'state.json', state)
            make_snapshot(cfg, root, repo, pending)
        payload = generation / 'payload'
        destination = cfg['remote'] + '/' + pending
        state['phase'] = 'uploading'
        atomic_json(STATE_DIR / 'state.json', state)
        run([RCLONE, 'copy', str(payload), destination, '--immutable'], cfg, network=True)
        state['phase'] = 'verifying'
        atomic_json(STATE_DIR / 'state.json', state)
        run([RCLONE, 'check', str(payload), destination, '--download', '--one-way'], cfg, network=True)
        marker = generation / '_COMPLETE.json'
        atomic_json(marker, {'owner': OWNER, 'generation': pending, 'verification': 'download-compared', 'completed_at': time.time()})
        run([RCLONE, 'copyto', str(marker), destination + '/_COMPLETE.json'], cfg, network=True)
        remote_marker = json.loads(run([RCLONE, 'cat', destination + '/_COMPLETE.json'], cfg, network=True))
        if remote_marker != read_json(marker):
            raise RuntimeError('completion marker verification failed')
        state.update(last_success_at=time.time(), last_generation=pending, pending=None, phase='complete')
        atomic_json(STATE_DIR / 'state.json', state)
        retention(cfg, root, pending)
        notify(state, 'vanpi iCloud backup verified', 'Weekly encrypted recovery copy uploaded and download-compared: ' + pending)
        return 'complete'
    finally:
        if mounted_here:
            # Ignition may already have removed the mount. Probe again and
            # never unmount a replacement filesystem accidentally.
            current = subprocess.run(['/usr/bin/findmnt', '-rn', '-M', str(mount), '-o', 'SOURCE'], capture_output=True, text=True)
            if current.returncode == 0:
                exact_mount(mount, label)
                subprocess.run(['/usr/bin/umount', str(mount)], check=True)
            elif current.returncode != 1 or current.stdout.strip() or current.stderr.strip():
                raise RuntimeError('cannot verify backup mount during cleanup')


def login(cfg):
    guard(cfg)
    p = Path(cfg['rclone_config'])
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if p.is_symlink():
        raise RuntimeError('unsafe credential path')
    os.umask(0o077)
    if credentials_ready(cfg):
        print('Renewing the existing iCloud login; approve 2FA privately.', flush=True)
        command = [RCLONE, 'config', 'reconnect', 'icloud:']
    else:
        print('Configure the remote named icloud, type iclouddrive, service drive. Enter Apple credentials only at the private prompts. Save and quit the menu to continue.', flush=True)
        command = [RCLONE, 'config']
    run(command, cfg, network=True, interactive=True, parked=False)
    verify_login(cfg)


def verify_login(cfg):
    """Test saved credentials without issuing a new interactive login."""
    if not credentials_ready(cfg):
        raise AuthenticationRequired('the icloud Drive remote needs login; run icloud_backup.sh --login')
    # A small round-trip check before enabling the first full repository upload.
    token = uuid.uuid4().hex
    local = STATE_DIR / ('canary-' + token + '.txt')
    local.write_text(token + '\n')
    remote = cfg['remote'] + '/_auth-check-' + token + '.txt'
    try:
        run([RCLONE, 'copyto', str(local), remote], cfg, network=True, parked=False)
        if run([RCLONE, 'cat', remote], cfg, network=True, parked=False) != token + '\n':
            raise RuntimeError('iCloud upload/download canary mismatch')
        run([RCLONE, 'deletefile', remote], cfg, network=True, parked=False)
    finally:
        local.unlink()
    atomic_json(STATE_DIR / 'authenticated.json', {'checked_at': time.time()})
    (STATE_DIR / 'authentication-error.json').unlink(missing_ok=True)
    print('iCloud authentication and upload/download check passed.', flush=True)
    subprocess.run(['/usr/bin/systemctl', 'start', '--no-block', 'vanpi-icloud-backup.service'], check=True)


def main():
    os.umask(0o077)
    signal.signal(signal.SIGTERM, abort)
    signal.signal(signal.SIGINT, abort)
    cfg = load_config()
    mode = sys.argv[1] if len(sys.argv) == 2 else '--run'
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE_DIR.is_symlink() or STATE_DIR.stat().st_uid != 0:
        raise RuntimeError('unsafe state directory')
    state = read_json(STATE_DIR / 'state.json', {})
    if mode == '--status':
        if (STATE_DIR / 'authentication-error.json').is_file():
            state['authentication_error'] = read_json(STATE_DIR / 'authentication-error.json')
        print(json.dumps(state, sort_keys=True, indent=2))
        return 0
    try:
        if mode == '--preflight':
            result = {'credentials_ready': credentials_ready(cfg), 'remote': cfg['remote'], 'keep_generations': cfg['keep_generations']}
            try:
                result['bind_address'] = guard(cfg)[0]
                result['uplink_allowed'] = True
            except Deferred as exc:
                result.update(uplink_allowed=False, reason=str(exc))
            print(json.dumps(result, sort_keys=True))
            return 0
        if mode == '--login':
            login(cfg)
            return 0
        if mode == '--verify-login':
            verify_login(cfg)
            return 0
        if mode != '--run':
            raise ValueError('unsupported mode')
        result = weekly(cfg, state)
        state.update(phase=result, last_attempt_at=time.time(), last_error=None)
        return 0
    except AuthenticationRequired as exc:
        message = str(exc)
        atomic_json(STATE_DIR / 'authentication-error.json',
                    {'failed_at': time.time(), 'reason': message})
        state.update(phase='authentication_required', last_attempt_at=time.time(), last_error=message)
        print('Authentication required: ' + message, file=sys.stderr, flush=True)
        if mode == '--run':
            notify(state, 'vanpi iCloud login needed', message)
        return 1
    except Deferred as exc:
        state.update(phase='deferred', last_attempt_at=time.time(), last_error=str(exc))
        print('Deferred: ' + str(exc), flush=True)
        if 'login' in str(exc):
            notify(state, 'vanpi iCloud login needed', 'Run the documented iCloud login command. No weekly cloud copy is current yet.')
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        state.update(phase='error', last_attempt_at=time.time(), last_error=message)
        print('Failed: ' + message, file=sys.stderr, flush=True)
        notify(state, 'vanpi iCloud backup needs attention', 'The weekly copy did not complete. Check service status; an expired Apple login may need renewal.')
        return 1
    finally:
        if mode == '--run':
            atomic_json(STATE_DIR / 'state.json', state)


if __name__ == '__main__':
    sys.exit(main())
