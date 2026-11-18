"""Calls to vanpi for a deferred Pi job: pi_agent.py and ssh, each under a hard
timeout capped by the caller's Budget, plus the parsers for what they print.

Used by the job's own pass (deferred_pi_run.py) and by the scheduler's control
verbs (deferred_pi_control.py)."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import time

from deferred_pi_state import RUN_ID

PI_HOST = 'vanpi'
LAUNCHER = Path.home() / '.claude/skills/pi-agent/pi_agent.py'
# The same options pi_agent.py uses, so both reach the Pi the same way.
SSH_OPTIONS = ['-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
               '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=4']
SSH_UNREACHABLE = 255
LAUNCHER_USAGE = 2  # pi_agent.py: usage or launch error, nothing was started

# Hard local timeouts per call; each sits above pi_agent.py's own ssh timeout
# (60 s for status, list and stop, 120 s to start a run) so the launcher
# normally gives up first and reports why.
SSH_TIMEOUT, STATUS_TIMEOUT, LAUNCH_TIMEOUT = 30, 90, 180
MIN_CALL = 5  # a call with less time left than this is not started
# Runs listed to find a launch token, widened once when the first listing is
# full; the Pi prunes runs after 30 days, so the larger one normally holds all.
LIST_LIMITS = (500, 2000)
SEARCH_FARTHER = 100000  # the printed command that searches past LIST_LIMITS
# After a stop the run needs a few seconds to end (SIGTERM, then the launcher
# records the exit), so its status is polled for this long before the stop
# counts as unconfirmed.
STOP_CONFIRM_WINDOW, STOP_CONFIRM_INTERVAL, STOP_MIN_READ = 30, 3, 10
# Tests shorten the window for the scheduler's cancel, which runs this module
# from the repository (a job's own copy is patched instead).
STOP_CONFIRM_WINDOW = float(os.environ.get('DEFERRED_PI_STOP_WINDOW') or STOP_CONFIRM_WINDOW)
STOP_CONFIRM_INTERVAL = float(os.environ.get('DEFERRED_PI_STOP_INTERVAL') or STOP_CONFIRM_INTERVAL)

STARTED = re.compile(r'^started (\S+) \(', re.M)
RUN_LINE = re.compile(r'^run:\s+(\S+) on \S')
STATE_LINE = re.compile(r'^state:\s+(done|running|died)(?:, exit (-?\d+))?(?:,|$)')
FINAL_DELIMITER = '--- final message'
NO_MESSAGE = '(none)'  # what the Pi prints for a run that left no result.md


class BudgetSpent(Exception):
    """The caller's time ran out before the next call could be made safely."""


class Budget:
    """Seconds left for a pass or a cancel; every remote call is capped by it."""

    def __init__(self, seconds):
        self.deadline = time.monotonic() + seconds

    def remaining(self):
        return self.deadline - time.monotonic()

    def cap(self, wanted):
        left = self.remaining()
        if left < MIN_CALL:
            raise BudgetSpent('the time left for this check ran out')
        return min(wanted, left)


def bounded(argv, timeout, data=None):
    """Runs argv under a hard timeout; None when it had to be killed."""
    try:
        return subprocess.run(argv, input=data, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def launcher(args, budget, wanted):
    return bounded(['/usr/bin/python3', '-I', '-B', str(LAUNCHER), *args], budget.cap(wanted))


def ssh(command, budget, data=b''):
    return bounded(['ssh', *SSH_OPTIONS, PI_HOST, command], budget.cap(SSH_TIMEOUT), data)


def text(result):
    return (result.stdout + result.stderr).decode('utf-8', 'replace').strip() if result else ''


def launcher_commands(run_id=None, token=None):
    base = f'/usr/bin/python3 {LAUNCHER}'
    if run_id:
        return f'Check it: {base} status {run_id}\nStop it: {base} stop {run_id}'
    return (f'Look for it: {base} status -n {SEARCH_FARTHER} | grep -e -{token}\n'
            f'Stop what it finds: {base} stop <run id>')


@dataclass(frozen=True)
class RunStatus:
    state: str          # done, running or died
    exit: str | None
    error: str | None
    final: str | None   # the text after the final-message delimiter; None while running
    text: str

    @property
    def report_blank(self):
        return not self.final or self.final.strip() in ('', NO_MESSAGE)

    @property
    def clean_finish(self):
        return self.state == 'done' and self.exit == '0' and not self.error

    @property
    def succeeded(self):
        return self.clean_finish and not self.report_blank


def parse_status(output, run_id):
    """A launcher `status ID` report for exactly that run, or None when it is not one."""
    lines = output.splitlines()
    if not lines or not (match := RUN_LINE.match(lines[0])) or match.group(1) != run_id:
        return None
    # The header ends at the first "--- " section; the agent's own text never counts as header.
    header = lines[:next((i for i, line in enumerate(lines) if line.startswith('--- ')), len(lines))]
    state = next((m for line in header if (m := STATE_LINE.match(line))), None)
    if state is None or (state.group(1) == 'done') != (state.group(2) is not None):
        return None
    error = next((line.split(':', 1)[1].strip() for line in header if line.startswith('error:')), None)
    final = None
    if state.group(1) != 'running':
        if FINAL_DELIMITER not in lines:
            return None
        final = '\n'.join(lines[lines.index(FINAL_DELIMITER) + 1:])
    return RunStatus(state.group(1), state.group(2), error or None, final, output)


def run_status(run_id, budget, wanted=STATUS_TIMEOUT):
    """A parsed status for run_id; None when the Pi or the report could not be read."""
    result = launcher(['status', run_id], budget, wanted)
    if result is None or result.returncode != 0:
        return None
    return parse_status(result.stdout.decode('utf-8', 'replace'), run_id)


@dataclass(frozen=True)
class TokenSearch:
    matches: list
    complete: bool  # the listing held every run on the Pi, so an absent token is really absent


def runs_with_token(token, budget, wanted=STATUS_TIMEOUT):
    """Run ids on the Pi ending in -TOKEN; None when the Pi could not be listed.

    A listing shorter than its limit holds every run; a full one is widened
    once, and a still-full listing is reported incomplete rather than absent.
    """
    for limit in LIST_LIMITS:
        result = launcher(['status', '-n', str(limit)], budget, wanted)
        if result is None or result.returncode != 0:
            return None
        ids = [line.split()[0] for line in result.stdout.decode('utf-8', 'replace').splitlines() if line.split()]
        ids = [run_id for run_id in ids if RUN_ID.match(run_id)]
        matches = [run_id for run_id in ids if run_id.endswith('-' + token)]
        if len(ids) < limit or matches:
            return TokenSearch(matches, len(ids) < limit)
    return TokenSearch([], False)


def stop_confirmed(run_id, budget, wanted=STATUS_TIMEOUT):
    """Sends the stop, then polls for a parsed status that no longer says running.

    Returns that status, or None while the stop is unconfirmed after
    STOP_CONFIRM_WINDOW. Only a positive terminal state confirms: an unreadable
    or malformed status is just another poll that did not confirm. It proves
    the launcher's recorded state, not that every detached descendant is gone.
    """
    launcher(['stop', run_id], budget, wanted)
    deadline = time.monotonic() + STOP_CONFIRM_WINDOW
    while True:
        remaining = deadline - time.monotonic()
        status = run_status(run_id, budget, min(wanted, max(STOP_MIN_READ, remaining)))
        if status and status.state != 'running':
            return status
        if time.monotonic() + STOP_CONFIRM_INTERVAL >= deadline:
            return None
        time.sleep(STOP_CONFIRM_INTERVAL)
