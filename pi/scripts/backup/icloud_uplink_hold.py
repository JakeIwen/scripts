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
    # A restarted batch delete re-lists and skips objects already gone (exit 0).
    'delete': frozenset(),
    'rmdir': frozenset({3}),
    'purge': frozenset({3}),
}


# Worker-registered writer of the hold heartbeat. None in manual commands and
# in the forked verifier, which never write worker state.
REPORTER = None
# Set once this run durably advanced (an object uploaded, verified or deleted);
# prerequisite reads never set it. It caps the quick retry of an expired hold.
ADVANCED = False
CHANGED_PATH = 'uplink or association changed during transfer'


def set_reporter(reporter):
    global REPORTER
    REPORTER = reporter


def note_advancement():
    global ADVANCED
    ADVANCED = True


def start_run(reporter):
    """A worker run begins: no advancement yet, heartbeat to this reporter."""
    global ADVANCED
    ADVANCED = False
    set_reporter(reporter)


def state_reporter(state, write):
    """Keep the heartbeat in worker state as `uplink_hold`; not transfer progress."""
    def report(value):
        if value is None:
            if state.pop('uplink_hold', None) is None:
                return
        else:
            state['uplink_hold'] = value
        write()
    return report


def fan_out(on_hold=None):
    """One hold's heartbeat to the registered reporter and an optional listener."""
    def report(value):
        if REPORTER is not None:
            REPORTER(value)
        if on_hold is not None:
            on_hold(value)
    return report


def check_running(probe, revalidate, proof_of, proof, work_check, refuse):
    """One probe for a stopped client; return only when it may resume.

    An unknown verdict, including evidence that went stale during the work
    check, raises UplinkLost so the caller kills the client and holds with
    nothing running: a client never resumes after one.
    """
    evidence, allowed, reason = probe()
    if allowed:
        if proof_of(evidence) != proof:
            raise refuse(CHANGED_PATH)
        work_check()
        # The work check can block (policyctl) past the evidence's freshness;
        # decide again on it so only still-valid proof resumes the client.
        allowed, reason = revalidate(evidence)
        if allowed:
            return
    if uplink_unknown(reason):
        raise UplinkLost(reason, time.monotonic(), time.time())
    raise refuse(reason)


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

    def admit(self, probe, revalidate, prepare=None):
        """Return (evidence, None) once permitted, else (None, the refusal reason).

        A permitted probe is followed by the work check, then the caller's
        `prepare` (anything that may block before its client starts, such as an
        activity callback), then a fresh decision on the same evidence. That
        decision is the last step before the caller starts a client; proof that
        went stale meanwhile counts as an unknown verdict and is re-probed.
        """
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
                    self.work_check()
                    if prepare is not None:
                        prepare()
                    allowed, reason = revalidate(evidence)
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
