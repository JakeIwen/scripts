#!/usr/bin/python3
"""Weekly Codex CLI update for the MacBook and vanpi, deferred while Starlink carries traffic.

`run` is the cron entry (every 6 h on both hosts). It runs `codex update` once
seven days have passed since the last good run, and defers to a later check when
the van's selected uplink is Starlink or cannot be verified. `--force` skips the
weekly wait, never the uplink check.

The Pi reads the uplink from backup/icloud_uplink.py's fresh route evidence; the
Mac asks the Pi when it is on the van LAN and treats any other network as off
the van's uplinks. `uplink` prints that verdict, and `status` prints this host's
last result (`--automation` adds the Pi's and prints the Mac Panel record).

Runs on /usr/bin/python3 3.9 (Mac) and 3.11 (Pi); stdlib only.
"""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable, Optional

WEEK_SECONDS = 7 * 86400
# A deferral this much older than the last good run turns the panel card amber.
OVERDUE_SECONDS = 14 * 86400
UPDATE_TIMEOUT_SECONDS = 900
VAN_ROUTER = '192.168.6.1'
PI_HOST = 'vanpi.lan'
PI_SCRIPT = '/home/pi/scripts/codex_update.py'
PI_UPLINK_EVIDENCE = '/home/pi/scripts/backup/icloud_uplink.py'
# The Starlink router's SSID, matched exactly.
STARLINK_SSID = 'denlink'
# mwan3 members that are Wi-Fi station uplinks; any other selected member
# (starwan, or one added later) is treated as possibly Starlink.
STATION_UPLINKS = ('wan', 'clientwan', 'lifiwan')
SSH_OPTIONS = ('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5')


class Outcome:
    UPDATED = 'updated'
    CURRENT = 'current'
    DEFERRED = 'deferred'
    FAILED = 'failed'


@dataclass
class Host:
    """What the job touches on this machine; tests pass stand-ins."""
    is_pi: bool
    codex: Path
    state_dir: Path
    run: Callable[..., subprocess.CompletedProcess]
    now: Callable[[], float] = time.time


def default_host():
    home = Path.home()
    return Host(is_pi=sys.platform.startswith('linux'), codex=home / '.local/bin/codex',
                state_dir=home / '.local/state/codex-update', run=subprocess.run)


def capture(host, argv, timeout):
    return host.run(list(argv), capture_output=True, text=True, timeout=timeout,
                    stdin=subprocess.DEVNULL)


def starlink_reason(evidence):
    """Why traffic may be riding Starlink, or None when every selected uplink is something else."""
    try:
        members = [m for m in evidence['router']['route_members'] if m['percent'] > 0]
        if not members:
            return 'no default-route evidence'
        for member in members:
            name = member['name']
            if name not in STATION_UPLINKS:
                return f'uplink {name} is selected'
            if name == 'wan':
                radio = evidence['ubnt']
                ssid = radio.get('ssid') if radio.get('connected') else None
            else:
                ssid = evidence['wireless_uplinks'].get(name, {}).get('ssid')
            if not isinstance(ssid, str) or not ssid.strip():
                return f'{name} has no verified SSID'
            if ssid == STARLINK_SSID:
                return f'{name} is on Starlink'
        return None
    except (KeyError, TypeError, AttributeError):
        return 'incomplete uplink evidence'


def pi_uplink(host):
    try:
        result = capture(host, ('/usr/bin/python3', '-I', '-B', PI_UPLINK_EVIDENCE), 40)
        evidence = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False, 'cannot read uplink evidence'
    if result.returncode != 0 or 'error' in evidence:
        return False, 'cannot read uplink evidence'
    reason = starlink_reason(evidence)
    return (False, reason) if reason else (True, 'no Starlink uplink selected')


def mac_gateway(host):
    try:
        result = capture(host, ('/sbin/route', '-n', 'get', 'default'), 5)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        key, _, value = line.strip().partition(':')
        if key == 'gateway':
            return value.strip()
    return None


def ask_pi(host, *args, timeout=60):
    """The Pi copy's JSON answer, or None when the Pi cannot be asked."""
    try:
        result = capture(host, ('/usr/bin/ssh', *SSH_OPTIONS, PI_HOST,
                                '/usr/bin/python3', '-I', '-B', PI_SCRIPT, *args), timeout)
        value = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return value if isinstance(value, dict) else None


def mac_uplink(host):
    if mac_gateway(host) != VAN_ROUTER:
        return True, 'not on the van LAN'
    verdict = ask_pi(host, 'uplink', '--json')
    if verdict is None or not isinstance(verdict.get('allowed'), bool):
        return False, 'cannot ask vanpi about the uplink'
    return verdict['allowed'], str(verdict.get('reason', ''))


def uplink(host):
    return pi_uplink(host) if host.is_pi else mac_uplink(host)


def read_state(host):
    try:
        value = json.loads((host.state_dir / 'state.json').read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_state(host, state):
    path = host.state_dir / 'state.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, sort_keys=True) + '\n')
    os.replace(temporary, path)


@contextmanager
def run_lock(host):
    host.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(host.state_dir / 'lock', 'w') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def codex_version(host):
    try:
        result = capture(host, (str(host.codex), '--version'), 30)
    except (OSError, subprocess.SubprocessError):
        return None
    words = result.stdout.split()
    return words[-1] if result.returncode == 0 and words else None


def codex_update(host):
    # The installer edits a shell profile unless its bin directory is on PATH,
    # and cron's PATH lacks it; non-interactive mode skips "Start Codex now?".
    env = dict(os.environ, PATH=f'{host.codex.parent}:/usr/bin:/bin:/usr/sbin:/sbin',
               CODEX_NON_INTERACTIVE='1')
    try:
        result = host.run([str(host.codex), 'update'], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=UPDATE_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as error:
        return False, f'codex update did not run: {error}'
    sys.stdout.write(result.stdout)
    sys.stdout.write(result.stderr)
    return result.returncode == 0, f'codex update exited {result.returncode}'


def stamp(host):
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(host.now()))


def run(host, force):
    with run_lock(host) as acquired:
        if not acquired:
            print(f'{stamp(host)} another run holds the lock')
            return 0
        state = read_state(host)
        last_success = state.get('last_success')
        if (not force and isinstance(last_success, (int, float))
                and host.now() - last_success < WEEK_SECONDS):
            return 0
        allowed, reason = uplink(host)
        state.update(last_check=host.now(), reason=reason)
        if not allowed:
            state['outcome'] = Outcome.DEFERRED
            write_state(host, state)
            print(f'{stamp(host)} deferred: {reason}')
            return 0
        before = codex_version(host)
        ok, detail = codex_update(host)
        after = codex_version(host)
        if ok and after:
            state.update(outcome=Outcome.UPDATED if after != before else Outcome.CURRENT,
                         last_success=host.now(), version=after)
        else:
            state.update(outcome=Outcome.FAILED, reason=detail, version=after or before)
        write_state(host, state)
        print(f"{stamp(host)} {state['outcome']}: {before} -> {after} ({state['reason']})")
        return 0 if state['outcome'] != Outcome.FAILED else 1


def host_fields(name, state, now):
    from tool_automation import status_field
    if not state:
        return 'idle', [status_field(name, 'not run yet', level='idle')]
    outcome, reason = state.get('outcome'), state.get('reason', '')
    last_success = state.get('last_success')
    if outcome == Outcome.FAILED:
        level = 'error'
    elif outcome == Outcome.DEFERRED and (
            not isinstance(last_success, (int, float)) or now - last_success > OVERDUE_SECONDS):
        level = 'warn'
    else:
        level = 'ok'
    fields = [status_field(f'{name} version', state.get('version') or 'unknown'),
              status_field(f'{name} last check', f'{outcome}: {reason}', state.get('last_check'), level)]
    if isinstance(last_success, (int, float)):
        fields.append(status_field(f'{name} last good run', time=last_success))
    return level, fields


def automation_record(host):
    from tool_automation import status_field, status_record
    levels, fields = [], []
    for name, state in (('Mac', read_state(host)), ('Pi', ask_pi(host, 'status', '--json', timeout=10))):
        if state is None:
            levels.append('unknown')
            fields.append(status_field(name, 'unreachable (off the van LAN?)', level='unknown'))
            continue
        level, rows = host_fields(name, state, host.now())
        levels.append(level)
        fields.extend(rows)
    order = ('error', 'warn', 'unknown', 'idle', 'ok')
    level = min(levels, key=order.index)
    summary = 'Weekly update, deferred while Starlink carries traffic'
    return status_record(level, summary, fields)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    run_parser = commands.add_parser('run', help='update when a week has passed and the uplink allows')
    run_parser.add_argument('--force', action='store_true', help='skip the weekly wait')
    uplink_parser = commands.add_parser('uplink', help='print whether an update may download now')
    uplink_parser.add_argument('--json', action='store_true')
    status_parser = commands.add_parser('status', help="print this host's last result")
    view = status_parser.add_mutually_exclusive_group()
    view.add_argument('--json', action='store_true')
    view.add_argument('--automation', action='store_true',
                      help='Mac Panel record for both hosts (needs shared/python on PYTHONPATH)')
    return parser


def main(argv=None, host=None):
    args = build_parser().parse_args(argv)
    host = host or default_host()
    if args.command == 'run':
        return run(host, args.force)
    if args.command == 'uplink':
        allowed, reason = uplink(host)
        print(json.dumps({'allowed': allowed, 'reason': reason}) if args.json
              else f"{'allowed' if allowed else 'deferred'}: {reason}")
        return 0
    if args.automation:
        print(json.dumps(automation_record(host)))
    else:
        state = read_state(host)
        print(json.dumps(state) if args.json else json.dumps(state, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
