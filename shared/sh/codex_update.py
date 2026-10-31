#!/usr/bin/python3
"""Weekly Codex and Claude CLI updates, admitted by host-local metering permissions.

`run` is the unchanged cron entry (every 6 h on both hosts). Each tool has its
own seven-day cadence and last result. `--force` skips the wait, never permission.
Claude's built-in updater is disabled on every scheduled check, even if its
weekly update is not due or permission is denied. Invalid settings stop only
that adapter; they are never overwritten.

`uplink --tool NAME` prints permission without changing settings or updating.
`status --json` prints keyed tool results; `--automation` adds the Pi's results
for the Mac Panel. The stable filename and Pi flat-copy contract are retained.
Runs on /usr/bin/python3 3.9 (Mac) and 3.11 (Pi); stdlib only.
"""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable

WEEK_SECONDS = 7 * 86400
# A deferral this much older than the last good run turns the panel card amber.
OVERDUE_SECONDS = 14 * 86400
UPDATE_TIMEOUT_SECONDS = 900
PERMISSION_TIMEOUT_SECONDS = 60
PI_HOST = 'vanpi.lan'
PI_SCRIPT = '/home/pi/scripts/codex_update.py'
PI_METERED = Path('/home/pi/scripts/vanpi-metered')
MAC_METERED = Path('/Users/jacobr/dev/scripts/macbook/metered/mac-metered')
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
    home: Path
    state_dir: Path
    metered_cli: Path
    run: Callable[..., subprocess.CompletedProcess]
    now: Callable[[], float] = time.time

    def binary(self, name):
        return self.home / '.local/bin' / name


def default_host():
    home = Path.home()
    is_pi = sys.platform.startswith('linux')
    return Host(is_pi=is_pi, home=home, state_dir=home / '.local/state/codex-update',
                metered_cli=PI_METERED if is_pi else MAC_METERED, run=subprocess.run)


def capture(host, argv, timeout):
    return host.run(list(argv), capture_output=True, text=True, timeout=timeout,
                    stdin=subprocess.DEVNULL)


def permission(host, name):
    consumer = f"{'pi' if host.is_pi else 'mac'}.{name}_update"
    try:
        result = capture(host, (str(host.metered_cli), 'allows', consumer),
                         PERMISSION_TIMEOUT_SECONDS)
        allowed = result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        allowed = False
    return allowed, 'permitted' if allowed else 'metered or unknown'


def ask_pi(host, *args, timeout=60):
    """The Pi copy's JSON answer, or None when the Pi cannot be asked."""
    try:
        result = capture(host, ('/usr/bin/ssh', *SSH_OPTIONS, PI_HOST,
                                '/usr/bin/python3', '-I', '-B', PI_SCRIPT, *args), timeout)
        value = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return value if isinstance(value, dict) else None


def tool_states(value):
    if not isinstance(value, dict):
        value = {}
    # e038d43 stored one flat Codex result; preserve its cadence on upgrade.
    if 'outcome' in value:
        value = {'codex': value}
    return {name: value.get(name, {}) if isinstance(value.get(name, {}), dict) else {}
            for name in TOOLS}


def read_state(host):
    try:
        value = json.loads((host.state_dir / 'state.json').read_text())
    except (OSError, ValueError):
        value = {}
    return tool_states(value)


def atomic_json(path, value, expected=None):
    """Standalone -I/flat-copy jobs cannot import the checkout's tool_settings writer."""
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(value, output, indent=2, allow_nan=False)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        if expected is not None:
            if path.is_symlink() or path.read_bytes() != expected:
                raise ValueError('settings changed during merge; retry next check')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_state(host, state):
    atomic_json(host.state_dir / 'state.json', state)


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


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def invalid_constant(value):
    raise ValueError('non-finite JSON number')


def prepare_claude(host):
    path = host.home / '.claude/settings.json'
    # Settings belong to the user, not to this job: refuse ambiguous/nonregular inputs.
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('Claude settings must be a regular file')
    raw = path.read_bytes()
    settings = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    if not isinstance(settings, dict) or not isinstance(settings.get('env', {}), dict):
        raise ValueError('Claude settings and env must be JSON objects')
    environment = settings.setdefault('env', {})
    if environment.get('DISABLE_AUTOUPDATER') == '1':
        return
    environment['DISABLE_AUTOUPDATER'] = '1'
    atomic_json(path, settings, expected=raw)


def prepare_codex(host):
    pass


@dataclass(frozen=True)
class Tool:
    label: str
    cadence: int
    prepare: Callable
    environment: dict


TOOLS = {
    'codex': Tool('Codex', WEEK_SECONDS, prepare_codex, {'CODEX_NON_INTERACTIVE': '1'}),
    'claude': Tool('Claude', WEEK_SECONDS, prepare_claude, {'DISABLE_AUTOUPDATER': '1'}),
}


def tool_version(host, name):
    try:
        result = capture(host, (str(host.binary(name)), '--version'), 30)
    except (OSError, subprocess.SubprocessError):
        return None
    words = result.stdout.split()
    # Claude includes a product label after its version; Codex prefixes its version.
    return next((word for word in words if word[:1].isdigit()), None) if result.returncode == 0 else None


def tool_update(host, name, adapter):
    # Codex edits a shell profile unless its bin directory is already on PATH.
    env = dict(os.environ, PATH=f'{host.binary(name).parent}:/usr/bin:/bin:/usr/sbin:/sbin',
               **adapter.environment)
    try:
        result = host.run([str(host.binary(name)), 'update'], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=UPDATE_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as error:
        return False, f'{name} update did not run: {error}'
    sys.stdout.write(result.stdout)
    sys.stdout.write(result.stderr)
    return result.returncode == 0, f'{name} update exited {result.returncode}'


def stamp(host):
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(host.now()))


def run_tool(host, name, adapter, state, force):
    try:
        adapter.prepare(host)
    except (OSError, ValueError) as error:
        # Do not log settings contents, which can contain private environment values.
        state.update(last_check=host.now(), outcome=Outcome.FAILED,
                     reason=f'{name} preparation failed ({type(error).__name__})')
        return 1
    last_success = state.get('last_success')
    if (not force and isinstance(last_success, (int, float))
            and host.now() - last_success < adapter.cadence):
        return 0
    allowed, reason = permission(host, name)
    state.update(last_check=host.now(), reason=reason)
    if not allowed:
        state['outcome'] = Outcome.DEFERRED
        print(f'{stamp(host)} {name} deferred: {reason}')
        return 0
    before = tool_version(host, name)
    ok, detail = tool_update(host, name, adapter)
    after = tool_version(host, name)
    if ok and after:
        state.update(outcome=Outcome.UPDATED if after != before else Outcome.CURRENT,
                     last_success=host.now(), version=after)
    else:
        state.update(outcome=Outcome.FAILED, reason=detail, version=after or before)
    print(f"{stamp(host)} {name} {state['outcome']}: {before} -> {after} ({state['reason']})")
    return 0 if state['outcome'] != Outcome.FAILED else 1


def run(host, force, names):
    with run_lock(host) as acquired:
        if not acquired:
            print(f'{stamp(host)} another run holds the lock')
            return 0
        state = read_state(host)
        codes = []
        for name in names:
            codes.append(run_tool(host, name, TOOLS[name], state[name], force))
            write_state(host, state)
        return max(codes)


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
    for machine, state in (('Mac', read_state(host)), ('Pi', ask_pi(host, 'status', '--json', timeout=10))):
        for name, adapter in TOOLS.items():
            label = f'{machine} {adapter.label}'
            if state is None:
                levels.append('unknown')
                fields.append(status_field(label, 'unreachable (off the van LAN?)', level='unknown'))
                continue
            level, rows = host_fields(label, tool_states(state)[name], host.now())
            levels.append(level)
            fields.extend(rows)
    order = ('error', 'warn', 'unknown', 'idle', 'ok')
    level = min(levels, key=order.index)
    return status_record(level, 'Weekly CLI updates, deferred when metered or unknown', fields)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    run_parser = commands.add_parser('run', help='check each tool independently once a week')
    run_parser.add_argument('--force', action='store_true', help='skip the weekly wait, never permission')
    run_parser.add_argument('--tool', choices=TOOLS, help='check only this tool (default: both)')
    uplink_parser = commands.add_parser('uplink', help='read host-local permission without updating')
    uplink_parser.add_argument('--tool', choices=TOOLS, default='codex')
    uplink_parser.add_argument('--json', action='store_true')
    status_parser = commands.add_parser('status', help="print this host's per-tool results")
    view = status_parser.add_mutually_exclusive_group()
    view.add_argument('--json', action='store_true')
    view.add_argument('--automation', action='store_true',
                      help='Mac Panel record for both hosts (needs shared/python on PYTHONPATH)')
    return parser


def main(argv=None, host=None):
    args = build_parser().parse_args(argv)
    host = host or default_host()
    if args.command == 'run':
        return run(host, args.force, (args.tool,) if args.tool else TOOLS)
    if args.command == 'uplink':
        allowed, reason = permission(host, args.tool)
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
