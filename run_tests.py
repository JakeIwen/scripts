#!/usr/bin/env python3
"""Run the repository's automated suites in parallel, failing fast.

State (durations, passed-tree fingerprints, lock) lives in the git common dir
so every worktree and concurrent session shares it and git never tracks it.
"""
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import argparse
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import BinaryIO


ROOT = Path(__file__).resolve().parent
LOADED_RUNNER = Path(__file__).read_bytes()
FRONTEND = Path('pi/apps/van_dashboard/frontend')
COMMAND_TIMEOUT = 900
CACHE_WINDOW = 6 * 3600
KEEP_PASSES = 50
VAN_COMPUTE_HELPER = ROOT / 'pi/tests/dashboard/_van_compute_checkout.py'
# Test-file prefix -> import roots its tests need, in search order.
PYTHON_IMPORT_ROOTS = {
    'macbook/bettertouchtool/tests': ('macbook/bettertouchtool',),
    'macbook/mac_panel/tests': ('macbook/mac_panel', 'shared/python'),
    'macbook/tests': ('shared/python', 'macbook/scripts', 'macbook/bettertouchtool'),
    'pi/tests': ('pi',),
    'pi/tests/backup': ('pi/scripts/backup',),
    'pi/tests/price_check': ('pi/scripts/price_check',),
    'pi/projects/phone-drop': ('pi/projects/phone-drop',),
    'shared/tests': ('shared/python',),
}


def repository_paths(root):
    records = git_output('ls-files', '-z', '--cached', '--others', '--exclude-standard', cwd=root)
    return sorted({Path(os.fsdecode(name)) for name in records.split(b'\0') if name
                   and not os.fsdecode(name).startswith('.claude/worktrees/')})


def test_files():
    """Nested agent worktrees have their own inventories, never the parent's."""
    return [path for path in repository_paths(ROOT) if (ROOT / path).is_file()
            and (path.name.startswith('test_') or '.test.' in path.name)]


def suite_commands(paths):
    plugins = set()
    frontend = False
    for path in paths:
        if path.suffix == '.py':
            # Independent projects import similarly named modules (including
            # other checkouts); a fresh interpreter prevents namespace leakage.
            module = '.'.join(path.with_suffix('').parts)
            yield str(path), [sys.executable, '-B', '-m', 'unittest', '-f', module], ROOT / path.parent
        elif path.suffix in {'.cjs', '.mjs'}:
            yield str(path), ['node', '--test', str(path)], ROOT
        elif path.suffix in {'.sh', '.zsh'}:
            interpreter = 'zsh' if path.suffix == '.zsh' else 'bash'
            yield str(path), [interpreter, str(path)], ROOT
        elif path.suffix in {'.ts', '.tsx'}:
            plugin = next((parent for parent in path.parents
                           if (ROOT / parent / '.claude-plugin/plugin.json').is_file()), None)
            if FRONTEND in path.parents:
                frontend = True
            elif plugin is not None:
                plugins.add(plugin)
            else:
                raise ValueError('No suite configured for ' + str(path))
        else:
            raise ValueError('No test runner configured for ' + str(path))
    for plugin in sorted(plugins):
        yield str(plugin), ['claude', 'plugin', 'test', str(plugin)], ROOT
    if frontend:
        yield str(FRONTEND), ['npm', 'test', '--', '--bail=1'], ROOT / FRONTEND


def git_output(*args, cwd=ROOT):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True).stdout


def state_dir():
    common = git_output('rev-parse', '--git-common-dir').decode().strip()
    directory = (ROOT / common).resolve() / 'run_tests'
    directory.mkdir(exist_ok=True)
    return directory


def read_json(path):
    """State is disposable; reject malformed shapes as well as malformed JSON."""
    try:
        values = json.loads(path.read_text())
        return {key: value for key, value in values.items()
                if type(value) in (int, float) and math.isfinite(value) and value >= 0}
    except (OSError, ValueError, AttributeError, OverflowError):
        return {}


def write_json(path, data):
    # This standalone runner also runs in empty fixture repos, without shared/.
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.')
    try:
        with os.fdopen(handle, 'w') as stream:
            json.dump(data, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def hash_value(digest, value):
    value = os.fsencode(str(value))
    digest.update(str(len(value)).encode() + b':' + value)


def file_signature(info):
    return info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def hash_path(digest, path, ancestors=()):
    """Read actual bytes: Git's stat cache and assume-unchanged bits aren't proof."""
    hash_value(digest, path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        hash_value(digest, 'missing')
        return
    # ctime also prevents an edit-and-revert during a run from earning a pass.
    hash_value(digest, file_signature(info))
    if stat.S_ISLNK(info.st_mode):
        hash_value(digest, os.readlink(path))
        target = path.resolve()
        if target in ancestors:
            raise ValueError(f'cyclic dependency symlink: {path}')
        hash_path(digest, target, ancestors + (path,))
    elif stat.S_ISREG(info.st_mode):
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        if file_signature(path.stat()) != file_signature(info):
            raise ValueError(f'file changed while fingerprinting: {path}')
    elif stat.S_ISDIR(info.st_mode):
        if path in ancestors:
            raise ValueError(f'cyclic dependency directory: {path}')
        if (path / '.git').exists():
            hash_repository(digest, path, ancestors + (path,))
        else:
            for child in sorted(path.iterdir()):
                # Vitest's result file and Vite's temporary configs are outputs.
                if child.name == '.git' or child.name == '.vite-temp':
                    continue
                if child.name == 'vitest' and path.name == '.vite':
                    continue
                hash_path(digest, child, ancestors + (path,))
    else:
        raise ValueError(f'unsupported cache input: {path}')


def hash_repository(digest, root, ancestors=()):
    hash_value(digest, root.resolve())
    # The index captures staging/gitlink changes, but never substitutes for bytes.
    digest.update(git_output('ls-files', '-s', '-z', cwd=root))
    paths = repository_paths(root)
    for path in paths:
        hash_path(digest, root / path, ancestors)
    dependencies = {root / '.local/test-venv'}
    dependencies.update(root / path.parent / 'node_modules'
                        for path in paths if path.name == 'package.json')
    for path in sorted(dependencies):
        hash_path(digest, path, ancestors)


def hash_van_compute(digest):
    """Use the tests' resolver; never silently turn resolver/Git errors into a key."""
    if not VAN_COMPUTE_HELPER.is_file():
        return
    spec = importlib.util.spec_from_file_location('_van_compute_checkout', VAN_COMPUTE_HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    checkout = module.CHECKOUT
    hash_value(digest, checkout)
    digest.update(git_output('rev-parse', 'HEAD', cwd=checkout))
    hash_repository(digest, checkout)


def tree_fingerprint():
    try:
        # A process waiting on the lock may still be executing the old runner.
        if Path(__file__).read_bytes() != LOADED_RUNNER:
            raise ValueError('runner source changed since launch; restart to cache a pass')
        digest = hashlib.sha256()
        hash_repository(digest, ROOT)
        # These change resolution or opt-in behavior without changing source files.
        for item in (sys.version, sys.executable, sys.platform, sys.prefix,
                     *(os.environ.get(key, '') for key in
                       ('BTT_LIVE_TESTS', 'PATH', 'PYTHONPATH', 'VAN_COMPUTE_CHECKOUT'))):
            hash_value(digest, item)
        if sys.prefix != sys.base_prefix and Path(sys.prefix) != ROOT / '.local/test-venv':
            hash_path(digest, Path(sys.prefix))
        for executable in (sys.executable, 'node', 'npm', 'claude', 'bash', 'zsh'):
            resolved = shutil.which(executable)
            hash_value(digest, (executable, resolved))
            if resolved:
                hash_path(digest, Path(resolved))
        hash_van_compute(digest)
        return digest.hexdigest()
    except Exception as error:
        # Resolver imports and disappearing inputs must disable reuse, not hide
        # an input behind an empty digest or prevent the real suite from running.
        print(f'Pass cache unavailable: {error}', flush=True)
        return None


@contextmanager
def exclusive_lock(path, check_interruption):
    with open(path, 'a') as stream:
        waiting = False
        while True:
            check_interruption()
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not waiting:
                    print('Waiting for another run_tests.py run to finish...', flush=True)
                    waiting = True
                time.sleep(0.1)
        # Test commands don't inherit the lock: a daemon leaked by one test would
        # otherwise hold it after the run and block every session's later runs.
        yield


def cached_pass(passes_file, fingerprint):
    recorded = read_json(passes_file).get(fingerprint)
    if recorded is not None and 0 <= time.time() - recorded < CACHE_WINDOW:
        return recorded
    return None


def report_cached(count, when):
    stamp = datetime.fromtimestamp(when).strftime('%H:%M')
    print(f'PASS: all {count} suite commands completed (cached: this exact tree '
          f'passed at {stamp}; --force reruns)', flush=True)


def command_environment(name):
    # Standalone scripts have their own import roots; keep this contract in
    # one runner instead of adding sys.path workarounds to individual tests.
    environment = dict(os.environ)
    roots = [str(ROOT)] + [str(ROOT / root) for prefix, prefix_roots in PYTHON_IMPORT_ROOTS.items()
                           if name.startswith(prefix + '/') for root in prefix_roots]
    environment['PYTHONPATH'] = os.pathsep.join(roots + [environment.get('PYTHONPATH', '')])
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    return environment


@dataclass
class CommandResult:
    name: str
    status: object
    secs: float
    output: str


@dataclass
class RunningCommand:
    name: str
    process: subprocess.Popen
    output: BinaryIO
    started: float


def live_group_members(group):
    listing = subprocess.run(['ps', '-A', '-o', 'pid=,pgid=,stat='],
                             capture_output=True, text=True, check=True).stdout
    rows = (line.split() for line in listing.splitlines())
    return [int(pid) for pid, pgid, state in rows
            if int(pgid) == group and not state.startswith('Z')]


def kill_group(process):
    """Kill the command's process group; return members still running."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS refuses killpg when every member left is a zombie; any member
        # still running is one we may not signal, so the run must not pass.
        process.kill()
        process.wait()
        return live_group_members(process.pid)
    process.wait()
    return []


def start_command(name, command, directory):
    # Files avoid pipe deadlocks and unbounded buffering while other jobs finish.
    output = tempfile.TemporaryFile()
    try:
        process = subprocess.Popen(
            command, cwd=directory, env=command_environment(name),
            stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        return RunningCommand(name, process, output, time.monotonic())
    except BaseException:
        output.close()
        raise


def finish_command(running, timeout):
    elapsed = time.monotonic() - running.started
    status = running.process.poll()
    if status is None and elapsed < timeout:
        return None
    if status is None:
        status = 'timeout'
    # A finished leader must not leave background descendants running.
    survivors = kill_group(running.process)
    running.output.seek(0)
    output = running.output.read().decode(errors='replace')
    running.output.close()
    if survivors:
        status = 'processes left running'
        output += f'run_tests.py could not kill processes {survivors} left by this command.\n'
    return CommandResult(running.name, status, elapsed, output)


def report_result(result):
    if result.status == 0:
        label = f'PASS {result.name} ({result.secs:.1f}s)'
    else:
        status = result.status if isinstance(result.status, str) else f'exit {result.status}'
        label = f'FAIL {result.name} ({status})'
    print(label, flush=True)
    if result.output:
        print(result.output.rstrip('\n'), flush=True)


def run_suite(commands, jobs, timings, timeout, check_interruption):
    """One scheduler owns admission/results, so workers can't race a stop flag."""
    pending = deque(sorted(commands, key=lambda item: -timings.get(item[0], float('inf'))))
    running, results, failures = [], [], []
    try:
        while pending or running:
            check_interruption()
            # Drain every observed completion before admitting even one new job.
            for job in running[:]:
                result = finish_command(job, timeout)
                if result is not None:
                    running.remove(job)
                    results.append(result)
                    if result.status != 0:
                        failures.append(result)
                    report_result(result)
            if failures:
                pending.clear()
            if pending and len(running) < jobs:
                name, command, directory = pending.popleft()
                try:
                    running.append(start_command(name, command, directory))
                except OSError as error:
                    result = CommandResult(name, 'launch error', 0, str(error))
                    results.append(result)
                    failures.append(result)
                    report_result(result)
                continue
            if running:
                time.sleep(0.02)
    finally:
        for job in running:
            survivors = kill_group(job.process)
            job.output.close()
            if survivors:
                print(f'run_tests.py could not kill processes {survivors} left by {job.name}.',
                      file=sys.stderr, flush=True)
    return results, failures, len(commands) - len(results)


def record_pass(passes_file, fingerprint):
    passes = read_json(passes_file)
    passes[fingerprint] = time.time()
    newest = sorted(passes.items(), key=lambda item: item[1], reverse=True)[:KEEP_PASSES]
    write_json(passes_file, dict(newest))


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('-j', '--jobs', type=int, default=os.cpu_count() or 1,
                        help='commands to run at once (default: CPU count)')
    parser.add_argument('--force', action='store_true',
                        help='ignore the pass cache and run the suite')
    parser.add_argument('--timeout', type=float, default=COMMAND_TIMEOUT,
                        help='per-command deadline in seconds (default: 900)')
    args = parser.parse_args(argv)
    if args.jobs < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('jobs and timeout must be positive, finite numbers')
    return args


class RunInterrupted(Exception):
    pass


@contextmanager
def interruption_handler():
    interrupted = []

    def request_stop(signum, _frame):
        # Don't raise between Popen and registering its process for cleanup.
        interrupted.append(signum)

    def check():
        if interrupted:
            raise RunInterrupted(interrupted[0])

    previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield check
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run_locked(args, state, check_interruption):
    live = os.environ.get('BTT_LIVE_TESTS') == '1'
    before = None if live else tree_fingerprint()
    # Discovery belongs inside the lock and after the initial fingerprint:
    # a waiter must not certify tests from an earlier inventory as this tree.
    paths = test_files()
    if not paths:
        raise ValueError('No tests discovered; refusing an empty success.')
    commands = list(suite_commands(paths))
    missing = [(name, command[0]) for name, command, _ in commands if not shutil.which(command[0])]
    if missing:
        raise ValueError('; '.join(f'{name} requires {executable}' for name, executable in missing))
    check_interruption()
    passes_file = state / 'passes.json'
    when = None if args.force or before is None else cached_pass(passes_file, before)
    if when is not None and tree_fingerprint() == before:
        check_interruption()
        report_cached(len(commands), when)
        return 0
    # A forced failure or interruption must revoke an older pass of this key.
    passes = read_json(passes_file) if before is not None else {}
    passes.pop(before, None)
    write_json(passes_file, passes)
    print(f'Discovered {len(paths)} test files; running {len(commands)} commands '
          f'with {args.jobs} jobs.', flush=True)
    timings_file = state / 'timings.json'
    timings = read_json(timings_file)
    results, failures, not_run = run_suite(
        commands, args.jobs, timings, args.timeout, check_interruption)
    check_interruption()
    timings.update({result.name: round(result.secs, 2) for result in results})
    write_json(timings_file, timings)
    if failures:
        # Name the failures on the last line: callers often keep only the tail.
        names = ', '.join(result.name for result in failures)
        print(f'FAILED: {len(failures)} command(s) failed ({names}); {not_run} were not run.',
              file=sys.stderr, flush=True)
        return 1
    after = None if before is None else tree_fingerprint()
    check_interruption()
    if before is not None and after == before:
        record_pass(passes_file, before)
    else:
        print('Inputs changed or cache unavailable/live; this pass was not cached.', flush=True)
    print(f'PASS: all {len(commands)} suite commands completed.', flush=True)
    return 0


def main(argv=None):
    args = parse_args(argv)
    sys.dont_write_bytecode = True
    try:
        with interruption_handler() as check_interruption:
            state = state_dir()
            with exclusive_lock(state / 'lock', check_interruption):
                return run_locked(args, state, check_interruption)
    except RunInterrupted as error:
        signum = error.args[0]
        print(f'FAILED: interrupted by signal {signum}.', file=sys.stderr, flush=True)
        return 128 + signum
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'FAILED: {error}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
