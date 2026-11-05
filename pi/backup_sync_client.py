"""Hold the existing Pi legacy fence shared throughout non-backup broad sync.

Python 3.9+ checkout CLI. Only inspection code is uploaded before readiness;
all unrelated uploads and package/compute phases run inside the held session.
"""
import argparse
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess

from pi.scripts.backup.backup_bootstrap_plan import SYNC_FILES, SYNC_READY, SYNC_RELEASE


def upload_boundary(repo, ssh):
    stage = subprocess.check_output(ssh + ['/usr/bin/mktemp', '-d', '/tmp/vanpi-sync-check.XXXXXX'],
                                    text=True, timeout=20).strip()
    if not re.fullmatch(r'/tmp/vanpi-sync-check\.[A-Za-z0-9]{6}', stage):
        raise ValueError('Unexpected sync inspection stage')
    source = repo / 'pi/scripts/backup'
    archive = subprocess.run(['/usr/bin/tar', '-cf', '-', '-C', str(source), *SYNC_FILES],
                             check=True, capture_output=True, timeout=20).stdout
    subprocess.run(ssh + ['/bin/tar', '-xf', '-', '-C', stage], input=archive, check=True, timeout=30)
    return stage + '/backup_deploy_sync.py'


def stop_child(child):
    if child is None:
        return
    # Its SSH/scp children share this private process group. Stop them before
    # closing the control channel that holds the host's existing lock inode.
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        child.wait(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()


def run_guarded(repo, target):
    if target.startswith('-') or re.search(r'\s', target):
        raise ValueError('Invalid SSH target')
    ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
           '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3', target]
    boundary = upload_boundary(repo, ssh)
    hold = subprocess.Popen(ssh + ['/usr/bin/sudo', '-n', '/usr/bin/python3', '-B', boundary, 'hold'],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
    child = None
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(hold.stdout, selectors.EVENT_READ)
            if not ready.select(timeout=45) or hold.stdout.readline() != (SYNC_READY + '\n').encode():
                raise RuntimeError('Host refused the broad-sync fence/readiness check')
        env = dict(os.environ, BACKUP_SYNC_REMOTE_BOUNDARY=boundary)
        child = subprocess.Popen(['/bin/bash', '-c',
                                  '. "$1/pi/sync_scripts.sh"; . "$1/pi/sync_workflow.sh"; run_sync_workflow',
                                  'guarded-sync', str(repo)], env=env, start_new_session=True,
                                 pass_fds=(hold.stdin.fileno(),))
        # An orphaned shell still owns the control pipe, so killing this client
        # cannot drop the host fence while that shell continues its uploads.
        while child.poll() is None:
            if hold.poll() is not None:
                raise RuntimeError('Lost the host sync fence; stopping remaining sync work')
            try:
                child.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                continue
        return child.returncode
    finally:
        stop_child(child)
        try:
            hold.stdin.write((SYNC_RELEASE + '\n').encode())
            hold.stdin.flush()
        except BrokenPipeError:
            pass
        hold.stdin.close()
        try:
            hold.wait(timeout=15)
        except subprocess.TimeoutExpired:
            hold.terminate()
            hold.wait(timeout=10)
        hold.stdout.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo', type=Path)
    parser.add_argument('target')
    args = parser.parse_args(argv)
    def interrupted(signum, _frame):
        raise InterruptedError('Sync interrupted by signal ' + str(signum))
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    try:
        return run_guarded(args.repo, args.target)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
