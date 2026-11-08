"""Runtime pieces of the shared-folder (SMB) transport: restic's rclone backend.

Validation of the remote.json record lives in offsite_config.validate_transport,
which the NAS receiver also installs; nothing here validates it again. restic
starts `<rclone> serve restic --stdio ...` itself and passes it its environment,
so the SMB credentials travel only in RCLONE_SMB_* variables, the password in
rclone's obscured form, and never in argv or a config file.
"""
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from offsite_config import PROXY_VARIABLES, RCLONE_VERSION
from offsite_repository import no_core_dumps

# What restic runs as `<rclone.program> <rclone.args> <remote>`. --append-only makes
# rclone refuse deleting repository data (locks excepted), so a buggy or hostile
# restic command cannot prune; a compromised Pi can still bypass it (plan review R8).
SERVE_ARGS = ('serve', 'restic', '--stdio', '--append-only')
# A lingering server is recognised by its program path followed by these arguments.
SERVE_PREFIX = SERVE_ARGS[:3]
CHILD_SECONDS = 10
REAP_SECONDS = 5
AMBIENT_PREFIXES = ('RCLONE_', 'RESTIC_')
OBSCURED_PATTERN = r'[A-Za-z0-9_-]{22,}'


def checked_program(rclone_path):
    # Absolute only: restic and subprocess would otherwise search PATH for "rclone".
    if not isinstance(rclone_path, str) or not os.path.isabs(rclone_path):
        raise ValueError('The rclone program must be an absolute path')
    return rclone_path


def restic_repository(transport):
    """restic strips `rclone:`; rclone gets the on-the-fly remote `:smb:<share>/<path>`."""
    return 'rclone::smb:{}/{}'.format(transport['share'], transport['path'])


def restic_options(rclone_path):
    return ['-o', 'rclone.program=' + checked_program(rclone_path), '-o', 'rclone.args=' + ' '.join(SERVE_ARGS)]


def minimal_environment():
    # No HOME, PATH or ambient RCLONE_*: nothing but the argv and stdin can steer rclone.
    return {'LC_ALL': 'C', 'RCLONE_CONFIG': ''}


def child(rclone_path, args, run, input_text=None):
    """One short rclone command; its output is never put in an error message."""
    try:
        return run([checked_program(rclone_path), *args], input=input_text, capture_output=True, text=True,
                   env=minimal_environment(), timeout=CHILD_SECONDS, check=False,
                   preexec_fn=no_core_dumps)  # obscure holds the DSM password
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError(f'Cannot run {rclone_path}; install rclone v{RCLONE_VERSION} there') from None


def obscure(rclone_path, password, run=subprocess.run):
    """rclone's reversible obscured form, which RCLONE_SMB_PASS must carry; password via stdin."""
    result = child(rclone_path, ('obscure', '-'), run, input_text=password + '\n')
    obscured = result.stdout.strip()
    if result.returncode or not re.fullmatch(OBSCURED_PATTERN, obscured) or obscured == password:
        raise RuntimeError(f'rclone obscure failed (exit {result.returncode})')
    return obscured


def check_rclone_version(rclone_path, run=subprocess.run):
    result = child(rclone_path, ('version',), run)
    if result.returncode or result.stdout.splitlines()[:1] != ['rclone v' + RCLONE_VERSION]:
        raise RuntimeError(f'{rclone_path} is not rclone v{RCLONE_VERSION}; reinstall it or update '
                           'offsite_config.RCLONE_VERSION after testing the new version in the Linux lane')


def without_ambient(base_env):
    """base_env minus every variable that could redirect restic, rclone or their traffic."""
    return {key: value for key, value in base_env.items()
            if not key.upper().startswith(AMBIENT_PREFIXES) and key.lower() not in PROXY_VARIABLES}


def rclone_environment(transport, obscured, base_env):
    # RCLONE_CONFIG='' loads no config file, so a stray rclone.conf cannot add or redirect remotes.
    return {**without_ambient(base_env), 'RCLONE_CONFIG': '', 'RCLONE_SMB_HOST': transport['address'],
            'RCLONE_SMB_PORT': str(transport['port']), 'RCLONE_SMB_USER': transport['user'],
            'RCLONE_SMB_PASS': obscured}


def lingering(processes, rclone_path):
    """PIDs among (pid, argv) rows that are restic servers started from rclone_path."""
    prefix = (rclone_path, *SERVE_PREFIX)
    return [pid for pid, argv in processes if tuple(argv[:len(prefix)]) == prefix]


def processes(proc=Path('/proc')):
    """(pid, argv) for every process whose command line is readable."""
    try:
        entries = [entry.name for entry in os.scandir(proc) if entry.name.isdigit()]
    except FileNotFoundError:
        if sys.platform.startswith('linux'):
            raise
        return []  # Non-Linux test hosts never run a real rclone server.
    rows = []
    for name in entries:
        try:
            raw = (proc / name / 'cmdline').read_bytes()
        except OSError:
            continue  # exited meanwhile, or a kernel thread
        argv = [part.decode('utf-8', 'surrogateescape') for part in raw.split(b'\0')]
        rows.append((int(name), argv[:-1] if argv and argv[-1] == '' else argv))
    return rows


def reap(rclone_path, *, listing=processes, kill=os.kill, clock=time.monotonic, sleep=time.sleep,
         grace=REAP_SECONDS):
    """TERM, then after grace KILL, every rclone server restic left running; returns the PIDs.

    restic starts rclone in its own process group, so stopping restic's group
    does not stop it; it normally exits when its stdio closes. The frozen path is
    unique to the run (job-offsite is a singleton), so no other run's server matches.
    """
    found = lingering(listing(), rclone_path)
    for pid in found:
        signal_quietly(kill, pid, signal.SIGTERM)
    deadline = clock() + grace
    remaining = found
    while remaining and clock() < deadline:
        sleep(0.1)
        remaining = [pid for pid in lingering(listing(), rclone_path) if pid in found]
    for pid in remaining:
        signal_quietly(kill, pid, signal.SIGKILL)
    if found:
        print(f'offsite: stopped {len(found)} rclone server(s) left after restic exited', file=sys.stderr, flush=True)
    return found


def signal_quietly(kill, pid, number):
    try:
        kill(pid, number)
    except ProcessLookupError:
        pass  # It exited between the listing and the signal.
