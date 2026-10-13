#!/usr/bin/env python3
"""Supervise the scheduled window, retaining all admission checks in each job."""
import argparse
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

import backup_priority as priority
import cloud_backup_control as controls
import local_fairness as fairness


SCRIPTS = {fairness.LocalJob.BORG: 'pi_backup.sh', fairness.LocalJob.EXFAT: 'exfat_snapshot.sh'}
POLL_SECONDS = 5


def cloud_stopped(service, command=subprocess.run):
    result = command(['/usr/bin/systemctl', 'show', service, '--property=ActiveState,MainPID,ControlPID'],
                     check=True, capture_output=True, text=True, timeout=10)
    fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    # Deactivating includes the verifier and ExecStopPost, not just the uploader.
    return (fields.get('ActiveState') in ('inactive', 'failed')
            and fields.get('MainPID') == '0' and fields.get('ControlPID') == '0')


class Window:
    def __init__(self, stamps, config, *, script_dir=None, command=subprocess.run,
                 popen=subprocess.Popen, clock=time.time, sleep=time.sleep, log=print):
        self.stamps, self.config = stamps, config
        self.script_dir = script_dir or Path(__file__).parent
        self.command, self.popen = command, popen
        self.clock, self.sleep, self.log = clock, sleep, log
        self.stopping = False

    def stop(self, *_):
        # A deadline or signal never terminates an already-admitted local job.
        # Disk lifecycle and explicit user-stop remain the job's own handlers.
        self.stopping = True

    def wait_cloud(self, turn, job):
        while fairness.admission_open(turn) and not self.stopping:
            if not fairness.heartbeat(turn['token'], self.clock()):
                return False
            if cloud_stopped(job.service, self.command):
                return fairness.admission_open(turn)
            self.sleep(min(POLL_SECONDS, fairness.admission_remaining(turn)))
        return False

    def run_job(self, kind, token=None):
        env = os.environ.copy()
        env.pop(fairness.TOKEN_ENV, None)
        if token:
            env[fairness.TOKEN_ENV] = token
        self.log('Starting ' + SCRIPTS[kind])
        child = self.popen([str(self.script_dir / SCRIPTS[kind])], env=env)
        while True:
            try:
                return child.wait(timeout=POLL_SECONDS)
            except subprocess.TimeoutExpired:
                if token:
                    try:
                        renewed = fairness.heartbeat(token, self.clock())
                    except (OSError, ValueError):
                        renewed = False
                    if not renewed:
                        self.log('Local fairness lease unavailable; waiting for the current local job only.')
                        self.stop()
                        token = None

    def local_jobs(self, kinds, turn=None):
        success = True
        for index, kind in enumerate(kinds):
            if self.stopping:
                return False
            token = turn['token'] if turn else None
            if turn and index and not self.stopping:
                renewed = fairness.next_admission(token, kind, self.config, now=self.clock())
                if renewed:
                    turn = renewed
            if turn and not fairness.admission_open(turn):
                self.log('Local fairness: admission window expired; no further local job will start.')
                return False
            if token and not fairness.heartbeat(token, self.clock()):
                return False
            before = None
            if turn:
                try:
                    before = self.stamps[kind].lstat()
                except FileNotFoundError:
                    pass
            rc = self.run_job(kind, token)
            if rc:
                self.log(f'{SCRIPTS[kind]} failed (status {rc}).')
                success = False
            if turn:
                # The scripts deliberately return zero for safe deferrals. Only
                # a new successful stamp counts as a successful fairness turn.
                # Compare the stamp itself, not its time against the turn's old
                # wall timestamp: the clock can step backward during a valid job.
                try:
                    after = self.stamps[kind].lstat()
                    completed = (stat.S_ISREG(after.st_mode)
                        and 0 <= self.clock() - after.st_mtime <= self.config.stale_hours * 3600
                        and (before is None or (after.st_ino, after.st_mtime_ns)
                             != (before.st_ino, before.st_mtime_ns)))
                except FileNotFoundError:
                    completed = False
                success = success and completed
        return success

    @fairness.supervisor_slot()
    def run(self):
        if priority.policy() == priority.PriorityMode.NORMAL:
            return 0 if self.local_jobs(SCRIPTS) else 1
        turn = fairness.reserve(self.stamps, self.config, now=self.clock(), log=self.log)
        if not turn:
            return 0
        job = priority.JOBS[turn['mode']]
        success, reason = False, 'local attempt failed or deferred'
        try:
            controls.pause_for_local_turn(directory=job.directory, service=job.service, command=self.command)
            if not self.wait_cloud(turn, job):
                reason = 'cloud admission window expired or cancelled'
                self.log('Local fairness: ' + reason + '; retry after backoff.')
                return 1
            success = self.local_jobs([fairness.LocalJob(k) for k in turn['jobs']], turn)
            if success:
                reason = 'scheduled local backups succeeded'
            return 0 if success else 1
        finally:
            fairness.finish(turn['token'], success, reason, now=self.clock())
            controls.resume_after_local_turn(directory=job.directory, service=job.service,
                                             command=self.command, now=self.clock())
            self.log('Local fairness: turn ended; independent cloud holds retained.')


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--borg-stamp', required=True, type=Path)
    parser.add_argument('--exfat-stamp', required=True, type=Path)
    parser.add_argument('--stale-hours', required=True, type=float)
    parser.add_argument('--admission-seconds', required=True, type=float)
    parser.add_argument('--backoff-hours', required=True, type=float)
    options = parser.parse_args(args)
    os.umask(0o077)
    window = Window({fairness.LocalJob.BORG: options.borg_stamp, fairness.LocalJob.EXFAT: options.exfat_stamp},
                    fairness.FairnessConfig(options.stale_hours, options.admission_seconds, options.backoff_hours))
    signal.signal(signal.SIGTERM, window.stop)
    signal.signal(signal.SIGINT, window.stop)
    try:
        return window.run()
    except BlockingIOError:
        print('Another scheduled window or guarded rollback is active; deferring.', flush=True)
        return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print('Local fairness unavailable: ' + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
