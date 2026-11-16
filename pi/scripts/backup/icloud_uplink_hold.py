#!/usr/bin/env python3
"""Bounded hold while a cloud worker's uplink verdict is unknown.

Invariant: no new cloud work starts while the verdict is unknown, and a
running command is stopped at its first unknown verdict (the caller kills
it). A hold only re-probes; it never permits anything itself. The worker
side (icloud_backup) supplies the probe, the parked/priority work check and
the state reporter; this module owns the deadline and restart rules.
"""
import time

from icloud_uplink import uplink_unknown

GRACE_KEY = 'uplink_unknown_grace_seconds'
DEFAULT_GRACE_SECONDS = 90
MAX_GRACE_SECONDS = 300
RECHECK_SECONDS = 5

# Commands a worker may restart from scratch after it killed them for an
# unknown verdict, mapped to the exit codes that mean "already done" on a
# restarted attempt only (the killed attempt may have finished server-side).
# rclone exits 4 for a missing file and 3 for a missing directory. hashsum
# is absent: its --output-file report is consumed while it runs.
RESTART_EXITS = {
    'lsjson': frozenset(),
    'cat': frozenset(),
    'copy': frozenset(),
    'copyto': frozenset(),
    'mkdir': frozenset(),
    'deletefile': frozenset({4}),
    'rmdir': frozenset({3}),
    'purge': frozenset({3}),
}


def apply_grace(cfg):
    """Default and validate the grace after every configuration overlay."""
    cfg.setdefault(GRACE_KEY, DEFAULT_GRACE_SECONDS)
    value = cfg[GRACE_KEY]
    if type(value) is not int or not 0 <= value <= MAX_GRACE_SECONDS:
        raise ValueError('invalid setting: ' + GRACE_KEY)
    return cfg


class UplinkLost(Exception):
    """A running command saw an unknown verdict; the caller stops it and holds."""

    def __init__(self, reason, started, since):
        super().__init__(reason)
        self.reason, self.started, self.since = reason, started, since


class UplinkHold:
    """One unknown-verdict episode: re-probe until verified, refused or out of time.

    The deadline runs from the first unknown verdict (`started`, monotonic).
    Probes are RECHECK_SECONDS apart and none starts at or after the deadline;
    the deadline is checked again after each work check, immediately before the
    probe. Grace 0 means one probe, then refuse, with no sleep and no work check.
    `slack` is the longest a probe plus the caller's final work check can overrun
    the deadline, so since + grace + slack bounds the whole hold; status uses it
    as the heartbeat's freshness limit.
    """

    def __init__(self, grace, work_check, report, slack, lost=None, log=None,
                 clock=None, wall=None, sleep=None):
        self.grace, self.work_check, self.report, self.slack = grace, work_check, report, slack
        # Resolved now, not at definition, so tests can patch print and the time module.
        self.log = log or print
        self.clock, self.wall, self.sleep = clock or time.monotonic, wall or time.time, sleep or time.sleep
        self.started = lost.started if lost else None
        self.since = lost.since if lost else None
        self.reason = lost.reason if lost else None

    def heartbeat(self):
        self.report({'reason': self.reason, 'since': self.since, 'checked_at': self.wall(),
                     'fresh_until': self.since + self.grace + self.slack})

    def deadline(self):
        return self.started + self.grace

    def admit(self, probe):
        """Return (evidence, None) once permitted, else (None, the refusal reason)."""
        outcome = 'stopped'
        try:
            while True:
                if self.started is not None:
                    if self.clock() + RECHECK_SECONDS >= self.deadline():
                        outcome = 'still unknown; deferring'
                        return None, self.reason
                    self.heartbeat()
                    self.sleep(RECHECK_SECONDS)
                    self.work_check()
                    if self.clock() >= self.deadline():
                        outcome = 'still unknown; deferring'
                        return None, self.reason
                    self.heartbeat()
                evidence, allowed, reason = probe()
                if allowed:
                    outcome = 'verified again'
                    return evidence, None
                if not uplink_unknown(reason):
                    outcome = 'refused: ' + reason
                    return None, reason
                if self.started is None:
                    self.started, self.since = self.clock(), self.wall()
                    self.log(f'Holding cloud work: {reason}; re-checking for up to {self.grace}s', flush=True)
                self.reason = reason
        finally:
            if self.started is not None:
                self.report(None)
                self.log(f'Uplink hold ended after {self.clock() - self.started:.0f}s: {outcome}', flush=True)
