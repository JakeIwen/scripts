"""linux-test-lane: run this checkout's Linux/Pi tests in a Debian VM on the Mac."""
from contextlib import contextmanager
from pathlib import Path
import argparse
import fcntl
import hashlib
import os
import shutil
import subprocess
import sys
import time

from run_tests import repository_paths
from . import lima
from .pins import HERE, PINS_FILE, read_pins
from .sync import (filter_rules, git_dirs, git_mirror_command, git_state_command,
                   source_mount, synced_paths, tree_command)

ROOT = HERE.parents[1]
PROVISION = HERE / 'provision.sh'
REQUIREMENTS = HERE / 'requirements.txt'
LOCK_FILE = Path.home() / '.local/state/linux-test-lane/lock'
STAMP_FILE = '/var/lib/linux-test-lane/provisioned'
# The lane covers code that runs on Linux: the Pi, shared modules and the
# Synology receiver, plus the runner itself. shared/claude-mods is excluded:
# its suites run through the Mac's `claude plugin test` and its codexclean
# build needs Node >= 22.13; the Pi only receives built copies.
DEFAULT_PATHS = ('pi', 'shared', 'synology', 'tests')
DEFAULT_EXCLUDES = ('shared/claude-mods',)


def log(message):
    print(f'linux-test-lane: {message}', file=sys.stderr, flush=True)


def vm_paths(pins):
    lane = f'{pins.home}/linux-test-lane'
    return f'{lane}/checkout', f'{lane}/venv'


def provision_stamp(venv):
    """Changes whenever provisioning inputs do, so `up` re-runs it only then."""
    digest = hashlib.sha256()
    for path in (PROVISION, PINS_FILE, REQUIREMENTS):
        digest.update(path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    digest.update(venv.encode())
    return digest.hexdigest()


@contextmanager
def lane_lock():
    # One VM checkout serves every worktree: syncing under a running suite
    # would change its files mid-run, so sync+run and stop/destroy serialize.
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, 'a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log('waiting for another lane command to finish...')
            fcntl.flock(stream, fcntl.LOCK_EX)
        yield


@contextmanager
def phase(name, timings):
    started = time.monotonic()
    yield
    timings.append(f'{name} {time.monotonic() - started:.1f}s')


def ensure_instance(pins, timings):
    template = lima.render_template(pins, source_mount(ROOT))
    info = lima.instance_info()
    if info is None:
        log(f'creating instance {lima.INSTANCE}')
        with phase('create', timings):
            lima.create(template)
        info = lima.instance_info()
    created = Path(info['dir']) / 'lima.yaml'
    if created.read_text() != template:
        raise ValueError(f'{lima.INSTANCE} was created from a different template (pins.env or '
                         'checkout changed); run `destroy` then `up` to recreate it')
    if info.get('status') != 'Running':
        log(f'starting instance {lima.INSTANCE} ({info.get("status")})')
        with phase('start', timings):
            lima.start()


def ensure_provisioned(pins, timings):
    _, venv = vm_paths(pins)
    stamp = provision_stamp(venv)
    current = lima.run_in_vm('cat', STAMP_FILE, capture=True, check=False).stdout.strip()
    if current == stamp:
        return
    log('provisioning (stamp changed)')
    with phase('provision', timings):
        lima.run_in_vm('sudo', 'bash', PROVISION, stamp, venv)


def up(pins, timings):
    ensure_instance(pins, timings)
    ensure_provisioned(pins, timings)


def sync(pins, timings):
    checkout, _ = vm_paths(pins)
    with phase('sync', timings):
        paths = synced_paths(repository_paths(ROOT), ROOT)
        lima.run_in_vm(*tree_command(ROOT, checkout), input=filter_rules(paths))
        common, git_dir = git_dirs(ROOT)
        lima.run_in_vm(*git_mirror_command(common, checkout))
        lima.run_in_vm(*git_state_command(git_dir, checkout))


def runner_arguments(args):
    selected = args.path or list(DEFAULT_PATHS)
    excluded = args.exclude_path if args.path or args.exclude_path else list(DEFAULT_EXCLUDES)
    result = [part for path in selected for part in ('--path', path)]
    result += [part for path in excluded for part in ('--exclude-path', path)]
    if args.force:
        result.append('--force')
    if args.jobs is not None:
        result += ['--jobs', str(args.jobs)]
    if args.timeout is not None:
        result += ['--timeout', str(args.timeout)]
    return result


def run_tests(pins, args, timings):
    checkout, venv = vm_paths(pins)
    with phase('suite', timings):
        return lima.run_in_vm(f'{venv}/bin/python', 'run_tests.py', *runner_arguments(args),
                              workdir=checkout, check=False).returncode


def status(pins):
    info = lima.instance_info()
    if info is None:
        print(f'{lima.INSTANCE}: not created (run `up`)')
        return 0
    print(f'{lima.INSTANCE}: {info.get("status")}, {info.get("cpus")} CPUs, '
          f'{info.get("memory", 0) // 2**30} GiB RAM, {info.get("disk", 0) // 2**30} GiB disk',
          flush=True)
    if info.get('status') != 'Running':
        return 0
    _, venv = vm_paths(pins)
    current = lima.run_in_vm('cat', STAMP_FILE, capture=True, check=False).stdout.strip()
    print('provisioning: ' + ('current' if current == provision_stamp(venv) else 'stale (run `up`)'),
          flush=True)
    lima.run_in_vm('sh', '-c', 'uptime; free -m | sed -n 1,2p; df -h / | tail -1')
    return 0


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest='command', required=True)
    commands.add_parser('up', help='create, start and provision the VM as needed')
    test = commands.add_parser('test', help='up, sync this working tree, run the suite in the VM')
    test.add_argument('--path', action='append', default=[],
                      help='run_tests.py --path (repeatable; default: ' + ', '.join(DEFAULT_PATHS) + ')')
    test.add_argument('--exclude-path', action='append', default=[],
                      help='run_tests.py --exclude-path (default with no --path: '
                      + ', '.join(DEFAULT_EXCLUDES) + ')')
    test.add_argument('--force', action='store_true', help='ignore the VM-side pass cache')
    test.add_argument('-j', '--jobs', type=int, help='parallel commands (default: VM CPUs)')
    test.add_argument('--timeout', type=float, help='per-command deadline in seconds')
    commands.add_parser('status', help='instance state, provisioning and VM load')
    commands.add_parser('shell', help='open a shell in the VM checkout')
    commands.add_parser('stop', help='stop the VM (it otherwise keeps running between runs)')
    commands.add_parser('destroy', help='delete the VM and its disk')
    return root


def dispatch(args, pins):
    timings = []
    try:
        if args.command == 'status':
            return status(pins)
        if args.command == 'shell':
            checkout, _ = vm_paths(pins)
            with lane_lock():
                ensure_instance(pins, timings)
            os.execvp(lima.LIMACTL, [lima.LIMACTL, 'shell', '--workdir', checkout, lima.INSTANCE])
        with lane_lock():
            if args.command == 'stop':
                if lima.instance_info() is not None:
                    lima.stop()
                return 0
            if args.command == 'destroy':
                if lima.instance_info() is not None:
                    lima.delete()
                return 0
            with phase('up', timings):
                up(pins, timings)
            if args.command == 'up':
                return 0
            sync(pins, timings)
            return run_tests(pins, args, timings)
    finally:
        if timings:
            log(', '.join(timings))


def main(argv=None):
    args = parser().parse_args(argv)
    if shutil.which(lima.LIMACTL) is None:
        print('linux-test-lane needs Lima: brew install lima', file=sys.stderr)
        return 1
    try:
        return dispatch(args, read_pins())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'linux-test-lane: FAILED: {error}', file=sys.stderr)
        return 1
