"""Metering holds never start/resume across a disk or ignition transition."""
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import subprocess

from shared.metered.records import Actual, Record
from shared.metered.store import RecordStore
from .config import (IGNITION_FLAG, INTERNET_SWITCHES, LIFECYCLE_LOCK, POLICYCTL,
                     POLICY_START, TORRENT_PROCESS)
from .torrent_api import STOPPED_STATES, TorrentApi, reconcile_api, tagged


@dataclass(frozen=True)
class FallbackState(Record):
    stopped_by_metering: bool = False


class TorrentHold:
    def __init__(self, state_dir, api=None, command=subprocess.run,
                 lifecycle_lock=LIFECYCLE_LOCK, ignition=IGNITION_FLAG):
        self.api, self.command = api or TorrentApi(), command
        self.lifecycle_lock, self.ignition = Path(lifecycle_lock), Path(ignition)
        self.fallback = RecordStore(Path(state_dir) / 'torrent-fallback.json',
                                    FallbackState, FallbackState)

    def _run(self, argv, timeout=5):
        return self.command(list(argv), capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=timeout)

    def _running(self):
        result = self._run(['/usr/bin/pgrep', '-x', TORRENT_PROCESS])
        if result.returncode not in (0, 1):
            raise RuntimeError('cannot inspect torrent process')
        return result.returncode == 0

    def _safe_to_resume(self):
        if self.ignition.exists():
            return False
        result = self._run([POLICYCTL, '--json', 'status'])
        if result.returncode:
            return False
        policy = json.loads(result.stdout)
        return (policy.get('disks_enabled') is True and policy.get('torrents_enabled') is True
                and 'movingparts' in policy.get('runtime', {}).get('mounted_disk_labels', []))

    def _terminate(self):
        if not self._running():
            return Actual.MANUAL
        with self.fallback.locked():
            self.fallback.save_locked(FallbackState(True))
        result = self._run(['/bin/bash', '-c', 'source "$1"; kill_torrent_client',
                            'metered-fallback', INTERNET_SWITCHES], timeout=35)
        return Actual.HELD if result.returncode == 0 and not self._running() else Actual.UNKNOWN

    def _release(self, reset_connections):
        descriptor = os.open(self.lifecycle_lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'r+b') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return Actual.UNKNOWN
            running = self._running()
            if running and reset_connections:
                # Old peer sessions can remain pinned after mwan3 changes policy.
                reconcile_api(self.api, False)
            if not self._safe_to_resume():
                return Actual.HELD if running and reset_connections else (
                    Actual.UNKNOWN if running else Actual.MANUAL)
            if not running:
                if self.fallback.load().stopped_by_metering:
                    self._run(POLICY_START)
                    return Actual.UNKNOWN
                return Actual.MANUAL
            rows = reconcile_api(self.api, True)
            with self.fallback.locked():
                self.fallback.save_locked(FallbackState(False))
            return Actual.RUNNING if any(row['state'] not in STOPPED_STATES for row in rows) else Actual.MANUAL

    def __call__(self, permitted, reset_connections=False):
        try:
            if permitted:
                return self._release(reset_connections)
            if not self._running():
                return Actual.HELD if self.fallback.load().stopped_by_metering else Actual.MANUAL
            rows = reconcile_api(self.api, False)
            return Actual.HELD if any(tagged(row) for row in rows) else Actual.MANUAL
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            if permitted and not reset_connections:
                return Actual.UNKNOWN
            try:
                return self._terminate()
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                return Actual.UNKNOWN
