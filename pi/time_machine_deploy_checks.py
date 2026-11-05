"""Time Machine target tests and their checkout-relative staging dependencies.

The deployment payload remains owned by backup_deploy_plan.sh. This manifest
adds only the sources needed to check that payload on the target runtime.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import shlex
import subprocess


# Each command is derived from its source, so staging and execution cannot drift.
TARGET_TESTS = (
    ('pi/tests/backup/test_time_machine_runtime.py',
     ('sudo', '-n', '/usr/bin/python3', '-B', '-m', 'unittest')),
    ('pi/tests/storage/test_backup_job_lock.sh', ('/bin/bash',)),
    ('pi/tests/storage/test_backup_locks.sh', ('/bin/bash',)),
    ('pi/tests/backup/test_backup_admission.py',
     ('/usr/bin/python3', '-B', '-m', 'unittest')),
)
SUPPORT_SOURCES = (
    'pi/__init__.py',
    'pi/tests/__init__.py',
    'pi/backup_local_deploy.sh',
    'pi/generate_backup_lock_registry.py',
    'pi/time_machine_deploy_checks.py',
    'pi/tests/lib.sh',
    'pi/tests/backup/backup_lock_fixtures.py',
    'pi/scripts/connectivity_status.py',
)
MAX_PARALLEL_TESTS = 3


def transfer_sources(repo):
    result = subprocess.run(
        ['/bin/bash', '-c', 'source "$1"; local_backup_sources tm',
         'deploy-check', str(repo / 'pi/scripts/backup/backup_deploy_plan.sh')],
        check=True, capture_output=True, text=True)
    sources = set(result.stdout.splitlines()) | set(SUPPORT_SOURCES)
    sources.update(source for source, _prefix in TARGET_TESTS)
    for source in sorted(sources):
        if not (repo / source).is_file():
            raise FileNotFoundError('Missing deploy-check source: ' + source)
    return sorted(sources)


def test_command(source, prefix):
    argument = source[:-3].replace('/', '.') if source.endswith('.py') else source
    return (*prefix, argument)


def run_target_tests(repo):
    # Both shell suites use private mktemp lock registries and process trees;
    # the root runtime test uses systemd-tmpfiles --root in its own temp tree.
    # Admission uses separate private registries too, and must run on Linux to
    # prove inherited descriptors through fdinfo rather than skip that proof.
    commands = [test_command(*test) for test in TARGET_TESTS]
    failed = False
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_TESTS) as executor:
        running = {executor.submit(subprocess.run, command, cwd=repo,
                                  capture_output=True, text=True): command
                   for command in commands}
        for future in as_completed(running):
            command = shlex.join(running[future])
            try:
                result = future.result()
            except OSError as error:
                print('FAIL ' + command + ': ' + str(error), flush=True)
                failed = True
                continue
            print(('PASS ' if result.returncode == 0 else 'FAIL ') + command
                  + ' (exit ' + str(result.returncode) + ')', flush=True)
            print(result.stdout + result.stderr, end='', flush=True)
            failed |= result.returncode != 0
    return int(failed)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--files', action='store_true', help='list complete transfer sources')
    mode.add_argument('--run', action='store_true', help='run isolated target tests, uncached')
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parent.parent
    if args.files:
        print('\n'.join(transfer_sources(repo)))
        return 0
    return run_target_tests(repo)


if __name__ == '__main__':
    raise SystemExit(main())
