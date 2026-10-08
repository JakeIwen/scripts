#!/usr/bin/python3
"""Close only the configured, idle Time Machine image for Pi offsite capture.

Installed as a root LaunchDaemon; SSH still runs as the installing Mac user.
Stops a backup only for an explicit, expiring capture request. Never force-ejects,
changes Time Machine scheduling/preferences or reads encryption keys.
"""
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
    destinations = plistlib.loads(command(['/usr/bin/tmutil', 'destinationinfo', '-X']))
    matching = []
    for destination in destinations.get('Destinations', []):
        url = urlsplit(destination.get('URL', ''))
        if (str(destination.get('ID', '')).lower() == destination_id[1].decode().lower()
                and destination.get('Kind') == 'Network' and url.scheme == 'smb'
                and url.hostname in PI_HOSTS and unquote(url.path) == '/mbp2tbkup'):
            matching.append(destination)
    if len(matching) != 1:
        raise RuntimeError('Active Time Machine destination is not the configured Pi share; stop refused')
    response = remote(cfg['user'], '--claim-capture-stop', request['generation'], request['capture_id'], grant['id'])
    if response.get('authorized') is not True or not time.time() < response.get('expires_at', 0) <= time.time() + 30:
        return 'Capture stop permission expired or was cancelled; backup left running.'
    command(['/usr/bin/tmutil', 'stopbackup'])
    # Cancellation can take time. A later normal poll must independently prove
    # idle state and clean detach before the Pi receives its acknowledgement.
    return 'Dashboard requested a graceful Time Machine stop; waiting for it to finish stopping.'


def coordinate(cfg):
    raw_status = command(['/usr/bin/tmutil', 'status'])
    running = re.search(rb'\bRunning\s*=\s*([01])\s*;', raw_status)
    if running is None:
        raise RuntimeError('Cannot establish Time Machine state')
    remote(cfg['user'], '--mac-present-v2')
    request = remote(cfg['user'], '--capture-request')
    name = request.get('generation', '')
    if not request.get('requested') or not GENERATION.fullmatch(name):
        return 'No offsite capture requested.'
    if request.get('expires_at', 0) < time.time() or request.get('bundle') != 'm4mac0.sparsebundle':
        raise RuntimeError('Invalid or expired capture request')
    capture_id = request.get('capture_id', '')
    if not re.fullmatch('[0-9a-f]{32}', capture_id):
        raise RuntimeError('Invalid capture nonce')
    if running.group(1) == b'1':
        return stop_for_capture(cfg, request, raw_status)
    images = matching_images(plistlib.loads(command(['/usr/bin/hdiutil', 'info', '-plist'])), request['bundle'])
    if len(images) > 1:
        raise RuntimeError('Ambiguous Time Machine image')
    if images:
        image = images[0]
        if image.get('image-encrypted') is not True:
            raise RuntimeError('Time Machine image is not encrypted')
        devices = [e.get('dev-entry', '') for e in image.get('system-entities', [])]
        devices = [d for d in devices if re.fullmatch(r'/dev/disk\d+', d)]
        if not devices:
            raise RuntimeError('No whole-image device available')
        # hdiutil itself refuses a busy image. Never add -force here.
        command(['/usr/bin/hdiutil', 'detach', devices[0]], timeout=90)
    if matching_images(plistlib.loads(command(['/usr/bin/hdiutil', 'info', '-plist'])), request['bundle']):
        raise RuntimeError('Time Machine image remains attached')
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


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--install']:
            install()
        elif not sys.argv[1:]:
            print(time.strftime('%Y-%m-%d %H:%M:%S'), coordinate(json.loads(CONFIG.read_text())), flush=True)
        else:
            raise RuntimeError('Usage: time_machine_offsite_coordinator.py [--install]')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        # Do not echo child stderr or SSH/account data into unattended logs.
        print('Offsite coordinator deferred: ' + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__), flush=True)
        sys.exit(1)
