"""Persistent DSM start/stop with explicit process identity and singleton ownership."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .configuration import DEFAULT_ROOT, STOP_SECONDS, ReceiverStatus, State, private_root, publish, read
from .runtime import process_identity, watch


def status(root) -> ReceiverStatus:
    record = read(root / 'status.json') or {'state': State.NOT_RUN}
    if not isinstance(record, dict) or record.get('state') not in {value.value for value in State}:
        raise ValueError('Invalid receiver status record')
    enabled = read(root / 'enabled.json') == {'enabled': True}
    pid = record.get('pid')
    observed = process_identity(pid) if type(pid) is int else None
    owned = observed is not None and record.get('identity') == observed
    server_pid = record.get('server_pid')
    server = process_identity(server_pid) if type(server_pid) is int else None
    owned_server = server is not None and record.get('server_identity') == server
    return {**record, 'enabled': enabled, 'owned_watcher': owned, 'owned_server': owned_server}


def launch(root):
    if status(root)['owned_watcher']:
        return
    with (root / 'watch.log').open('ab') as log:
        subprocess.Popen([sys.executable, '-B', '-m', __package__, '--root', str(root), 'watch'],
                         cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, close_fds=True)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if status(root)['owned_watcher']:
            return
        time.sleep(0.1)
    raise RuntimeError('Receiver watcher failed to start; inspect private watch.log')


def stop(root):
    # The enable record, not a signal to a possibly reused PID, controls stop.
    publish(root / 'enabled.json', {'enabled': False})
    deadline = time.monotonic() + STOP_SECONDS + 10
    while time.monotonic() < deadline:
        fd = os.open(root / 'watch.lock', os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                pass
        finally:
            os.close(fd)
        time.sleep(0.1)
    raise RuntimeError('Owned watcher/server has not stopped; do not unplug or replace files')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('command', choices=('prepare', 'status', 'start', 'stop', 'restart', 'boot', 'watch'))
    args = parser.parse_args(argv)
    try:
        root = private_root(args.root)
        if args.command == 'watch':
            return watch(root)
        if args.command == 'status':
            value = status(root)
            print(json.dumps(value, sort_keys=True))
            return 0 if value['enabled'] and value['owned_watcher'] and value['owned_server'] and value['state'] == State.SERVING else 1
        fd = os.open(root / 'control.lock', os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if args.command == 'prepare':
                from .configuration import config
                from .binaries import verify
                config(root)
                verify(root)
                print('Prepared only; DSM boot task is not registered by this command.')
            elif args.command == 'stop':
                stop(root)
            elif args.command in ('start', 'restart'):
                if args.command == 'restart':
                    stop(root)
                publish(root / 'enabled.json', {'enabled': True})
                launch(root)
            elif args.command == 'boot' and read(root / 'enabled.json') == {'enabled': True}:
                launch(root)
        finally:
            os.close(fd)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print('offsite receiver: operation failed; inspect private configuration, installation and watcher status', file=sys.stderr)
        return 1
