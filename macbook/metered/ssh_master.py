"""The metered sync's private SSH master: route pinning, options and best-effort teardown."""
from dataclasses import dataclass
import os
import signal
import stat
import subprocess

from shared.sh.peer_copy import SSH_OPTIONS
from .config import (ALIVE_COUNT_MAX, ALIVE_INTERVAL, CONTROL_PATH, CONTROL_PERSIST,
                     PI_HOST, TEARDOWN_TIMEOUT)

SSH = '/usr/bin/ssh'
PS = '/bin/ps'
# ssh prints this when the socket exists but its master won't answer (seen with a
# SIGSTOPped master): it then dials directly and succeeds, so neither a timeout nor
# exit 255 reports the wedge, and without a teardown every later tick pays a login.
MUX_BYPASSED = 'disabling multiplexing'


@dataclass(frozen=True)
class Route:
    """One resolution of vanpi: ~/.ssh/config may pick LAN or Tailscale per invocation,
    so the request and its teardown must share this answer instead of re-resolving."""
    hostname: str
    control_path: str


def resolve_route(run):
    """Ask `ssh -G` once; None when it fails, so the caller syncs without a master."""
    try:
        result = run([SSH, '-G', *SSH_OPTIONS, '-o', 'ControlPath=' + CONTROL_PATH, PI_HOST],
                     timeout=TEARDOWN_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    found = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(' ')
        found.setdefault(key, value.strip())
    hostname, path = found.get('hostname'), found.get('controlpath')
    if not hostname or not path or path == 'none':
        return None
    return Route(hostname, path)


def route_options(route):
    # Command-line options beat ~/.ssh/config, which pins this call to the resolved route.
    return ['-o', 'HostName=' + route.hostname, '-o', 'ControlPath=' + route.control_path]


def mux_options(route):
    return [*route_options(route),
            '-o', 'ControlMaster=auto',
            '-o', 'ControlPersist=%d' % CONTROL_PERSIST,
            '-o', 'ServerAliveInterval=%d' % ALIVE_INTERVAL,
            '-o', 'ServerAliveCountMax=%d' % ALIVE_COUNT_MAX]


def socket_inode(path):
    try:
        status = os.lstat(path)
    except OSError:
        return None
    return status.st_ino if stat.S_ISSOCK(status.st_mode) else None


def kill_master_by_title(run, route):
    """SIGKILL works on a SIGSTOPped master that cannot answer `-O exit`."""
    title = 'ssh: %s [mux]' % route.control_path
    result = run([PS, '-axo', 'pid=,args='], timeout=TEARDOWN_TIMEOUT)
    if result.returncode != 0:
        return
    for line in result.stdout.splitlines():
        pid, _, args = line.strip().partition(' ')
        if args.strip() == title and pid.isdigit():
            os.kill(int(pid), signal.SIGKILL)


def tear_down_master(run, route):
    """Drop a wedged master so the next tick dials fresh; never raises, each step bounded."""
    try:
        result = run([SSH, '-O', 'exit', *SSH_OPTIONS, *route_options(route), PI_HOST],
                     timeout=TEARDOWN_TIMEOUT)
        if result.returncode == 0:
            return
    except (OSError, subprocess.SubprocessError):
        pass
    inode = socket_inode(route.control_path)
    try:
        kill_master_by_title(run, route)
    except (OSError, subprocess.SubprocessError):
        pass
    # Unlink only the socket we saw: a replacement master may already own the path.
    if inode is not None and socket_inode(route.control_path) == inode:
        try:
            os.unlink(route.control_path)
        except OSError:
            pass
