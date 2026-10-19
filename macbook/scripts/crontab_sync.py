#!/usr/bin/python3
"""Revision-checked edits and drift-safe installation of macbook/crontab."""
import argparse
from collections import Counter
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_TEMPFAIL = 75

OFF_PREFIX = '#off '
BEGIN = re.compile(r'^# BEGIN managed (.+)$')
END = re.compile(r'^# END managed (.+)$')
ASSIGNMENT = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$')
VARIABLE = re.compile(r'\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))')
JOB_ID = re.compile(r'^[0-9a-f]{12}$')
MANAGED_SWITCHES = {'llm-restore': 'llmrestore --cron'}
BACKUP_LIMIT = 20

REPO_ENV = 'CRONTAB_SYNC_FILE'
STATE_ENV = 'CRONTAB_SYNC_STATE_DIR'
BINARY_ENV = 'CRONTAB_SYNC_CRONTAB'
DEFAULT_REPO = Path(__file__).resolve().parents[1] / 'crontab'
DEFAULT_STATE = Path.home() / '.local/state/crontab-sync'
DEFAULT_BINARY = Path('/usr/bin/crontab')
GIT = Path('/usr/bin/git')


class SyncFailure(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def configuration():
    return (Path(os.environ.get(REPO_ENV, str(DEFAULT_REPO))).expanduser(),
            Path(os.environ.get(STATE_ENV, str(DEFAULT_STATE))).expanduser(),
            Path(os.environ.get(BINARY_ENV, str(DEFAULT_BINARY))).expanduser())


def normalized_lines(text):
    return [line.rstrip() for line in text.splitlines() if line.strip()]


def drift(live, repo):
    live_lines, repo_lines = Counter(normalized_lines(live)), Counter(normalized_lines(repo))
    return list((live_lines - repo_lines).elements()), list((repo_lines - live_lines).elements())


def revision(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        raise SyncFailure(EXIT_ERROR, 'Crontab content is not valid UTF-8') from None


def expand_value(value, variables, home):
    value = VARIABLE.sub(lambda match: variables.get(match[1] or match[2], match[0]), value)
    if value == '~' or value.startswith('~/'):
        value = str(home) + value[1:]
    return value


def redirected_log(command, variables, home):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars='><&|;')
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    for index, token in enumerate(tokens[:-1]):
        if token not in ('>', '>>'):
            continue
        value = expand_value(tokens[index + 1], variables, home)
        if '$' in value or value == '/dev/null' or not Path(value).is_absolute():
            continue
        return value
    return None


def split_cron_line(line):
    stripped = line.strip()
    parts = stripped.split(None, 1 if stripped.startswith('@') else 5)
    expected = 2 if stripped.startswith('@') else 6
    if len(parts) != expected:
        return None
    return parts[:-1], parts[-1]


def stable_job_id(line):
    return hashlib.sha256(line.strip().encode('utf-8')).hexdigest()[:12]


def line_content(raw_line):
    if raw_line.endswith('\r\n'):
        return raw_line[:-2]
    if raw_line.endswith(('\n', '\r')):
        return raw_line[:-1]
    return raw_line


def parse_jobs(text, home=None):
    home = home or Path.home()
    variables = {'HOME': str(home)}
    block, comment, jobs = None, '', []
    for index, raw_line in enumerate(text.splitlines(keepends=True)):
        original = line_content(raw_line)
        stripped = original.strip()
        if match := BEGIN.fullmatch(stripped):
            block, comment = match[1], ''
            continue
        if END.fullmatch(stripped):
            block, comment = None, ''
            continue
        paused = original.startswith(OFF_PREFIX)
        candidate = original[len(OFF_PREFIX):] if paused else original
        if not paused and stripped.startswith('#'):
            comment = stripped[1:].strip()
            continue
        if not stripped:
            comment = ''
            continue
        if not paused and (match := ASSIGNMENT.fullmatch(original)):
            raw_value = match[2]
            if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in ('"', "'"):
                raw_value = raw_value[1:-1]
            variables[match[1]] = expand_value(raw_value, variables, home)
            comment = ''
            continue
        parsed = split_cron_line(candidate)
        if parsed is not None:
            schedule_fields, command = parsed
            jobs.append({
                'id': stable_job_id(candidate),
                'schedule_fields': schedule_fields,
                'command': command,
                'paused': paused,
                'managed_block': block,
                'managed_switch': MANAGED_SWITCHES.get(block),
                'description': comment,
                'log_path': redirected_log(command, variables, home),
                '_line_index': index,
            })
        comment = ''
    return jobs


def public_job(job):
    return {key: value for key, value in job.items() if not key.startswith('_')}


def run_tool(argv, timeout=30):
    try:
        return subprocess.run([str(item) for item in argv], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise SyncFailure(EXIT_ERROR, 'Command timed out') from None
    except OSError as exc:
        raise SyncFailure(EXIT_ERROR, str(exc)) from None


def tool_error(result):
    raw = result.stderr or result.stdout
    return raw.decode('utf-8', errors='replace').strip() or f'Command exited {result.returncode}'


def read_live(binary):
    result = run_tool((binary, '-l'))
    if result.returncode == 0:
        return result.stdout
    message = tool_error(result)
    if result.returncode == 1 and 'no crontab for' in message.lower():
        return b''
    raise SyncFailure(EXIT_ERROR, message)


def install_file(binary, repo):
    result = run_tool((binary, repo))
    if result.returncode != 0:
        raise SyncFailure(EXIT_ERROR, tool_error(result))


def git_uncommitted(repo):
    root_result = run_tool((GIT, '-C', repo.parent, 'rev-parse', '--show-toplevel'))
    if root_result.returncode != 0:
        raise SyncFailure(EXIT_ERROR, tool_error(root_result))
    root = Path(root_result.stdout.decode('utf-8', errors='strict').strip()).resolve()
    try:
        relative = repo.resolve().relative_to(root)
    except ValueError:
        raise SyncFailure(EXIT_ERROR, f'{repo} is outside its Git worktree') from None
    result = run_tool((GIT, '-C', root, 'status', '--porcelain', '--', relative))
    if result.returncode != 0:
        raise SyncFailure(EXIT_ERROR, tool_error(result))
    return bool(result.stdout.strip())


def ensure_private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


@contextmanager
def sync_lock(state):
    ensure_private_directory(state)
    descriptor = os.open(state / 'lock', os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise SyncFailure(EXIT_TEMPFAIL, 'Another crontab-sync command is running') from None
            raise
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def atomic_write(path, raw, mode):
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=str(path.parent))
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, 'wb') as output:
            descriptor = -1
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def save_live_backup(state, raw):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    path = state / f'live-{stamp}.crontab'
    atomic_write(path, raw, 0o600)
    backups = sorted(state.glob('live-*.crontab'), reverse=True)
    for old in backups[BACKUP_LIMIT:]:
        old.unlink()
    return path


def require_revision(raw, expected):
    actual = revision(raw)
    if expected is not None and expected != actual:
        raise SyncFailure(EXIT_TEMPFAIL,
                          f'macbook/crontab changed: expected revision {expected}, found {actual}')
    return actual


def require_installable(live_text, repo_text):
    live_only, repo_only = drift(live_text, repo_text)
    if live_only:
        raise SyncFailure(EXIT_REFUSED,
                          'Install refused: the live crontab has lines missing from macbook/crontab',
                          live_only=live_only, repo_only=repo_only)
    return live_only, repo_only


def status_payload(repo, binary):
    raw = repo.read_bytes()
    repo_text = decode(raw)
    live_text = decode(read_live(binary))
    live_only, repo_only = drift(live_text, repo_text)
    return {
        'revision': revision(raw),
        'uncommitted': git_uncommitted(repo),
        'jobs': [public_job(job) for job in parse_jobs(repo_text)],
        'live_only': live_only,
        'repo_only': repo_only,
        'installable': not live_only,
    }


def select_job(jobs, job_id):
    if not JOB_ID.fullmatch(job_id):
        raise SyncFailure(EXIT_REFUSED, f'Unknown job id: {job_id}')
    matches = [job for job in jobs if job['id'] == job_id]
    if not matches:
        raise SyncFailure(EXIT_REFUSED, f'Unknown job id: {job_id}')
    if len(matches) != 1:
        raise SyncFailure(EXIT_REFUSED, f'Ambiguous job id: {job_id}')
    return matches[0]


def replace_job_line(text, job, pause):
    lines = text.splitlines(keepends=True)
    index = job['_line_index']
    content = line_content(lines[index])
    ending = lines[index][len(content):]
    if pause:
        replacement = OFF_PREFIX + content
    else:
        if not content.startswith(OFF_PREFIX):
            raise SyncFailure(EXIT_ERROR, 'Paused job marker is missing')
        replacement = content[len(OFF_PREFIX):]
    lines[index] = replacement + ending
    return ''.join(lines).encode('utf-8')


def rollback_repo(repo, installed_raw, original_raw, mode):
    try:
        if repo.read_bytes() == installed_raw:
            atomic_write(repo, original_raw, mode)
    except OSError:
        pass


def edit_job(repo, state, binary, action, job_id, expected_revision):
    original_raw = repo.read_bytes()
    require_revision(original_raw, expected_revision)
    repo_text = decode(original_raw)
    live_raw = read_live(binary)
    require_installable(decode(live_raw), repo_text)
    job = select_job(parse_jobs(repo_text), job_id)
    if job['managed_switch']:
        setting = 'off' if action == 'pause' else 'on'
        raise SyncFailure(EXIT_REFUSED,
                          f'{job["managed_block"]} owns its own switch; use '
                          f'{job["managed_switch"]} {setting}')
    pause = action == 'pause'
    if job['paused'] == pause:
        state_name = 'paused' if pause else 'active'
        return {'message': f'Job {job_id} is already {state_name}',
                'revision': revision(original_raw),
                'uncommitted': git_uncommitted(repo)}
    new_raw = replace_job_line(repo_text, job, pause)
    if repo.read_bytes() != original_raw:
        raise SyncFailure(EXIT_TEMPFAIL, 'macbook/crontab changed during the edit')
    mode = stat.S_IMODE(repo.stat().st_mode)
    atomic_write(repo, new_raw, mode)
    try:
        save_live_backup(state, live_raw)
        install_file(binary, repo)
    except (SyncFailure, OSError):
        rollback_repo(repo, new_raw, original_raw, mode)
        raise
    state_name = 'paused' if pause else 'resumed'
    return {'message': f'Job {job_id} {state_name}', 'revision': revision(new_raw),
            'uncommitted': git_uncommitted(repo)}


def install(repo, state, binary, expected_revision):
    raw = repo.read_bytes()
    current_revision = require_revision(raw, expected_revision)
    repo_text = decode(raw)
    live_raw = read_live(binary)
    require_installable(decode(live_raw), repo_text)
    if repo.read_bytes() != raw:
        raise SyncFailure(EXIT_TEMPFAIL, 'macbook/crontab changed during install')
    save_live_backup(state, live_raw)
    install_file(binary, repo)
    return {'message': 'Installed macbook/crontab', 'revision': current_revision,
            'uncommitted': git_uncommitted(repo)}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    status = commands.add_parser('status', help='show tracked jobs and live drift')
    status.add_argument('--json', action='store_true')
    for name in ('pause', 'resume'):
        command = commands.add_parser(name, help=f'{name} one tracked job and install')
        command.add_argument('job')
        command.add_argument('--revision', required=True)
        command.add_argument('--json', action='store_true')
    install_parser = commands.add_parser('install', help='install when no live lines would be lost')
    install_parser.add_argument('--revision')
    install_parser.add_argument('--json', action='store_true')
    return parser


def print_plain(command, value):
    if command != 'status':
        print(value['message'])
        return
    print(f"revision: {value['revision']}")
    print(f"uncommitted: {'yes' if value['uncommitted'] else 'no'}")
    print(f"installable: {'yes' if value['installable'] else 'no'}")
    for job in value['jobs']:
        state = 'paused' if job['paused'] else 'active'
        schedule = ' '.join(job['schedule_fields'])
        managed = f" [{job['managed_block']}]" if job['managed_block'] else ''
        print(f"{job['id']}  {state:6}  {schedule}  {job['command']}{managed}")
    if value['live_only']:
        print('Live-only lines:')
        for line in value['live_only']:
            print(f'  {line}')
    if value['repo_only']:
        print('Repo-only lines:')
        for line in value['repo_only']:
            print(f'  {line}')


def emit(command, value, as_json):
    if as_json:
        print(json.dumps(value, sort_keys=True))
    else:
        print_plain(command, value)


def main(argv=None):
    args = build_parser().parse_args(argv)
    repo, state, binary = configuration()
    try:
        # Status only reads, so a running edit never turns the panel's poll into an error.
        with nullcontext() if args.command == 'status' else sync_lock(state):
            if args.command == 'status':
                value = status_payload(repo, binary)
            elif args.command == 'install':
                value = install(repo, state, binary, args.revision)
            else:
                value = edit_job(repo, state, binary, args.command, args.job, args.revision)
        emit(args.command, value, args.json)
        return EXIT_OK
    except SyncFailure as exc:
        value = {'error': exc.message, **exc.details}
        if args.json:
            print(json.dumps(value, sort_keys=True))
        else:
            print(exc.message, file=sys.stderr)
            for line in exc.details.get('live_only', []):
                print(f'  {line}', file=sys.stderr)
        return exc.code
    except (OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({'error': str(exc)}, sort_keys=True))
        else:
            print(str(exc), file=sys.stderr)
        return EXIT_ERROR


if __name__ == '__main__':
    raise SystemExit(main())
