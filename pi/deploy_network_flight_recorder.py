#!/usr/bin/env python3
"""Helpers deploy_network_storage.py embeds; the original recorder installer is retired.

That installer's check/apply/rollback shipped the recorder together with the
flat dashboard backend, its unit and its React release, which the van-dashboard
repo now deploys, and its saved manifests include them. deploy_network_storage.py
owns the recorder (check/apply --recorder-only for routine changes).
"""
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


# Importing the redaction shim also imports the CLI and its package dependencies.
# These are checked prerequisites, never managed deployment targets.
MONITOR_V4_MODULES = (
    '__init__', 'cli', 'common', 'crash', 'daemon', 'journal', 'probes', 'report', 'rollups', 'store',
)
# The complete package before per-disk history; manifests saved then pin exactly this.
MONITOR_V5_MODULES = (*MONITOR_V4_MODULES, 'coalesce', 'locking', 'maintenance')
MONITOR_PACKAGE_DEPENDENCIES = {
    f'pi/scripts/system_monitor/{name}.py': f'/home/pi/scripts/system_monitor/{name}.py'
    for name in (*MONITOR_V5_MODULES, 'disk_history')
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    with Path(path).open('rb') as stream:
        result = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
        return result.hexdigest()


def regular_info(path):
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f'refusing non-regular managed file: {path}')
    return dict(sha256=digest(path), mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)


def command(args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check)


def service_state(name):
    enabled = command(['/usr/bin/systemctl', 'is-enabled', name], False)
    active = command(['/usr/bin/systemctl', 'is-active', name], False)
    return dict(enabled=enabled.stdout.strip() or 'not-found', active=active.stdout.strip() == 'active')


def atomic_file(source, destination, metadata):
    destination = Path(destination)
    # Refuse symlinked path components, including the managed root.
    if destination.parent.resolve() != destination.parent:
        raise ValueError('symlinked managed destination directory')
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.network-install-', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output, Path(source).open('rb') as incoming:
            shutil.copyfileobj(incoming, output)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, metadata['mode'])
        os.chown(temporary, metadata['uid'], metadata['gid'])
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def systemctl(*args):
    return command(['/usr/bin/systemctl', *args])


def main():
    sys.exit('deploy_network_flight_recorder.py is retired: its targets and saved manifests '
             'include the dashboard, which the van-dashboard repo (~/dev/van-dashboard) now '
             'deploys. Use deploy_network_storage.py check/apply --recorder-only for recorder '
             'changes.')


if __name__ == '__main__':
    main()
