#!/usr/bin/python3
"""Close only the configured, idle Time Machine image for Pi offsite capture.

Installed as a root LaunchDaemon; SSH still runs as the installing Mac user.
Stops a backup only for an explicit, expiring capture request. Never force-ejects,
changes Time Machine scheduling/preferences or reads encryption keys.
"""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit

BASE = Path('/Library/Application Support/vanpi-time-machine-offsite')
CONFIG = BASE / 'config.json'
LABEL = 'com.jacobr.time-machine-offsite'
PLIST = Path('/Library/LaunchDaemons') / (LABEL + '.plist')
REMOTE = '/home/pi/scripts/backup/time_machine_icloud.py'
GENERATION = re.compile(r'tm-\d{8}T\d{6}Z-[0-9a-f]{8}\Z')
PI_HOSTS = ('vanpi._smb._tcp.local.', 'vanpi.lan', 'vanpi.local', '192.168.6.103')
TMUTIL = '/usr/bin/tmutil'
HDIUTIL = '/usr/bin/hdiutil'
DISKUTIL = '/usr/sbin/diskutil'
MOUNT = '/sbin/mount'
LOG_TEXT_LIMIT = 160
DEVICE = re.compile(r'/dev/disk\d+(?:s\d+)*\Z')


def log_text(value):
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    # Strip terminal escapes and controls before selecting one bounded line.
    value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value or '')
    for line in value.splitlines():
        line = ' '.join(''.join(c if c.isprintable() else ' ' for c in line).split())
        if line:
            line = re.sub(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+', '<url>', line)
            line = re.sub(r'\S+@\S+', '<account>', line)
            return line[:LOG_TEXT_LIMIT]
    return ''


class StepFailure(RuntimeError):
    def __init__(self, label, cause, local=False):
        self.cause = cause
        self.reason = log_text(getattr(cause, 'stderr', None)) if local else ''
        message = label + ': ' + type(cause).__name__
        if isinstance(cause, subprocess.CalledProcessError):
            message += ' exit ' + str(cause.returncode)
        if self.reason:
            message += ': ' + self.reason
        super().__init__(message)


@contextmanager
def step(label, local=False):
    try:
        yield
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # Only fixed local-tool steps may expose a sanitized stderr line. Never
        # stringify subprocess errors: their command includes SSH/account data.
        raise StepFailure(label, exc, local) from exc


def command(args, timeout=30):
    return subprocess.run(args, capture_output=True, timeout=timeout, check=True).stdout


def remote(user, *args):
    # Fixed host/helper and strictly validated generation arguments, no shell payload.
    return json.loads(command(['/usr/bin/sudo', '-H', '-u', user, '/usr/bin/ssh',
        '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=yes',
        'pi@vanpi.lan', 'sudo', '-n', '/usr/bin/python3', REMOTE, *args]))


def matching_images(data, bundle):
    result = []
    for image in data.get('images', []):
        parts = Path(image.get('image-path', '')).parts
        if (len(parts) == 7 and parts[:3] == ('/', 'Volumes', '.timemachine') and
                parts[3].lower() in PI_HOSTS and
                parts[-2:] == ('mbp2tbkup', bundle)):
            result.append(image)
    return result


def stop_for_capture(cfg, request, raw_status):
    grant = request.get('stop_request')
    if not grant:
        return 'Waiting for the active Time Machine backup to finish.'
    if (not isinstance(grant, dict) or not re.fullmatch('[0-9a-f]{32}', grant.get('id', ''))
            or not time.time() < grant.get('expires_at', 0) <= time.time() + 1800):
        raise RuntimeError('Invalid capture stop permission')
    destination_id = re.search(rb'\bDestinationID\s*=\s*"?([0-9A-Fa-f-]{36})"?\s*;', raw_status)
    if destination_id is None:
        raise RuntimeError('Cannot identify the active Time Machine destination; stop refused')
    with step('destinationinfo', local=True):
        destinations = plistlib.loads(command([TMUTIL, 'destinationinfo', '-X']))
    matching = []
    for destination in destinations.get('Destinations', []):
        url = urlsplit(destination.get('URL', ''))
        if (str(destination.get('ID', '')).lower() == destination_id[1].decode().lower()
                and destination.get('Kind') == 'Network' and url.scheme == 'smb'
                and url.hostname in PI_HOSTS and unquote(url.path) == '/mbp2tbkup'):
            matching.append(destination)
    if len(matching) != 1:
        raise RuntimeError('Active Time Machine destination is not the configured Pi share; stop refused')
    with step('claim-stop'):
        response = remote(cfg['user'], '--claim-capture-stop', request['generation'], request['capture_id'], grant['id'])
    if response.get('authorized') is not True or not time.time() < response.get('expires_at', 0) <= time.time() + 30:
        return 'Capture stop permission expired or was cancelled; backup left running.'
    with step('tmutil-stopbackup', local=True):
        command([TMUTIL, 'stopbackup'])
    # Cancellation can take time. A later normal poll must independently prove
    # idle state and clean detach before the Pi receives its acknowledgement.
    return 'Dashboard requested a graceful Time Machine stop; waiting for it to finish stopping.'


def image_mounts(image):
    devices = {e.get('dev-entry', '') for e in image.get('system-entities', [])}
    devices = {device for device in devices if DEVICE.fullmatch(device)}
    with step('mounts'):
        raw = command([MOUNT]).decode('utf-8', errors='strict')
    mounts = []
    for line in raw.splitlines():
        device, separator, rest = line.partition(' on ')
        # APFS snapshot mounts, including Time Machine's backup-snapshot mounts,
        # are listed as <snapshot name>@<device>; the device decides ownership.
        if device.rpartition('@')[2] not in devices:
            continue
        mountpoint, options, tail = rest.rpartition(' (')
        if not separator or not options or not tail.endswith(')') or not mountpoint.startswith('/'):
            raise RuntimeError('Cannot establish Time Machine image mounts')
        mounts.append(mountpoint)
    return sorted(set(mounts), key=lambda path: (-len(Path(path).parts), path))


def diskutil_dissenter(error):
    # diskutil sometimes reports its dissenter on stdout, not stderr. Retain
    # only that diagnostic line, never arbitrary successful command output.
    for value in (getattr(error, 'stderr', None), getattr(error, 'stdout', None)):
        if isinstance(value, bytes):
            value = value.decode('utf-8', errors='replace')
        for line in (value or '').splitlines():
            if re.search(r'\bdissent(?:er|ed)\b', line, re.IGNORECASE):
                return log_text(line)
    return ''


def unmount_snapshots(image, notes):
    primary = {e.get('mount-point') for e in image.get('system-entities', [])}
    for mountpoint in image_mounts(image):
        if mountpoint in primary:
            continue
        # The image's reported primary mounts stay hdiutil's responsibility;
        # only additional mounts on those exact devices are cleaned up here.
        try:
            with step('diskutil-unmount', local=True):
                command([DISKUTIL, 'unmount', mountpoint])
        except StepFailure as exc:
            note = str(exc) + '; mount=' + log_text(mountpoint)
            dissenter = diskutil_dissenter(exc.cause)
            if dissenter and dissenter != exc.reason:
                note += '; dissenter=' + dissenter
            notes.append(note)
        else:
            notes.append('diskutil-unmount: ' + log_text(mountpoint) + ': unmounted')


def record_remaining_mounts(image, notes):
    try:
        mounts = image_mounts(image)
    except RuntimeError as exc:
        notes.append(str(exc))
    else:
        if mounts:
            notes.append('remaining mounts: ' + ', '.join(log_text(path) for path in mounts))


def detach_image(image, notes):
    if image.get('image-encrypted') is not True:
        raise RuntimeError('Time Machine image is not encrypted')
    devices = [e.get('dev-entry', '') for e in image.get('system-entities', [])]
    devices = [d for d in devices if re.fullmatch(r'/dev/disk\d+', d)]
    if not devices:
        raise RuntimeError('No whole-image device available')
    unmount_snapshots(image, notes)
    for attempt in range(2):
        try:
            # hdiutil itself refuses a busy image. Never add -force here.
            with step('hdiutil-detach', local=True):
                command([HDIUTIL, 'detach', devices[0]], timeout=90)
            return
        except StepFailure as exc:
            busy = (isinstance(exc.cause, subprocess.CalledProcessError) and
                    (exc.cause.returncode == errno.EBUSY or 'busy' in exc.reason.lower()))
            if attempt or not busy:
                record_remaining_mounts(image, notes)
                raise
            # A snapshot can appear between mount discovery and detach. Retry
            # once, without escalating to a forced unmount or forced detach.
            notes.append(str(exc) + '; rechecking snapshot mounts once')
            unmount_snapshots(image, notes)


def attached_images(bundle):
    with step('hdiutil-info', local=True):
        data = plistlib.loads(command([HDIUTIL, 'info', '-plist']))
    return matching_images(data, bundle)


def coordinate(cfg, notes=None):
    if notes is None:
        notes = []
    with step('tmutil-status', local=True):
        raw_status = command([TMUTIL, 'status'])
    running = re.search(rb'\bRunning\s*=\s*([01])\s*;', raw_status)
    if running is None:
        raise RuntimeError('Cannot establish Time Machine state')
    with step('heartbeat'):
        remote(cfg['user'], '--mac-present-v2')
    with step('capture-request'):
        request = remote(cfg['user'], '--capture-request')
    name = request.get('generation', '')
    if not request.get('requested') or not GENERATION.fullmatch(name):
        return 'No offsite capture requested.'
    if request.get('expires_at', 0) < time.time() or request.get('bundle') != 'm4mac0.sparsebundle':
        raise RuntimeError('Invalid or expired capture request')
    capture_id = request.get('capture_id', '')
    if not re.fullmatch('[0-9a-f]{32}', capture_id):
        raise RuntimeError('Invalid capture nonce')
    if request.get('acknowledged') is True:
        return 'Capture in progress; image stays detached.'
    if running.group(1) == b'1':
        return stop_for_capture(cfg, request, raw_status)
    images = attached_images(request['bundle'])
    if len(images) > 1:
        raise RuntimeError('Ambiguous Time Machine image')
    if images:
        detach_image(images[0], notes)
    if attached_images(request['bundle']):
        raise RuntimeError('Time Machine image remains attached')
    with step('capture-ready'):
        response = remote(cfg['user'], '--capture-ready', name, capture_id)
    if not response.get('accepted'):
        raise RuntimeError('Pi declined the capture acknowledgement')
    return 'Image cleanly detached; Pi may capture it. Normal backups resume after capture.'


def install():
    if os.geteuid() != 0 or not os.environ.get('SUDO_USER') or os.environ['SUDO_USER'] == 'root':
        raise RuntimeError('Install using sudo from your normal Mac account')
    user = os.environ['SUDO_USER']
    pwd.getpwnam(user)
    # Confirm that this daemon can use the existing SSH login before installing.
    with step('capture-request'):
        remote(user, '--capture-request')
    if BASE.is_symlink() or PLIST.is_symlink():
        raise RuntimeError('Unsafe installation path')
    BASE.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chown(BASE, 0, 0)
    script = BASE / 'coordinator.py'
    for p in (script, CONFIG):
        if p.is_symlink():
            raise RuntimeError('Unsafe installed file')
    shutil.copyfile(__file__, script)
    script.chmod(0o755)
    CONFIG.write_text(json.dumps({'user': user}) + '\n')
    CONFIG.chmod(0o600)
    # Idempotent update of this dedicated daemon only; unrelated jobs untouched.
    subprocess.run(['/bin/launchctl', 'bootout', 'system/' + LABEL], capture_output=True)
    with PLIST.open('wb') as f:
        plistlib.dump({'Label': LABEL, 'ProgramArguments': ['/usr/bin/python3', str(script)],
                      'RunAtLoad': True, 'StartInterval': 120,
                      'ProcessType': 'Background', 'LowPriorityIO': True,
                      'StandardOutPath': str(BASE / 'coordinator.log'),
                      'StandardErrorPath': str(BASE / 'coordinator.log')}, f)
    PLIST.chmod(0o644)
    command(['/bin/launchctl', 'bootstrap', 'system', str(PLIST)])
    print('Installed the offsite capture coordinator (checks every two minutes).')


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    notes = []
    try:
        if args == ['--install']:
            install()
        elif not args:
            message = coordinate(json.loads(CONFIG.read_text()), notes)
            print(time.strftime('%Y-%m-%d %H:%M:%S'), '; '.join(notes + [message]), flush=True)
        else:
            raise RuntimeError('Usage: time_machine_offsite_coordinator.py [--install]')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print('Offsite coordinator deferred: ' + '; '.join([message] + notes), flush=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
