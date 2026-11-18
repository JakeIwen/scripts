"""Durable state of a `deferred.zsh add-agent --host vanpi` job, and its trusted reader.

One file per fact in the job directory, each written atomically; their
presence is the state. The job's own `run` (a copy of deferred_pi_run.py)
writes them during its passes, and the scheduler's control verbs
(deferred_pi_control.py) write the collection-deadline failure and clear an
attempt the user replaced. deferred.zsh and the list assembler read them
through `snapshot()` from the repository's copy of this module, so listing,
scheduling and cancelling never execute anything the job directory holds.

`snapshot()` is the one judge of whether the records are consistent: every
field present and typed, and only the combinations a pass interrupted between
two writes can leave. Anything else raises StateError, which the listing shows
as an invalid row, the checker flags without running the job, plain cancel
refuses and retry/run-now refuse whatever `--relaunch` id is given: records that
disagree cannot say which attempt the user would be replacing, so only
`cancel --abandon` removes such a job.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import re
import time

SCHEMA = 1

# Exit statuses shared by the run script, the control verbs and deferred.zsh.
DONE, FAILED, USAGE, NOT_READY = 0, 1, 2, 75
REFUSED = 6           # cancel: a Pi run may still be active (deferred.zsh relays it)
RELAUNCH_REFUSED = 7  # retry/run-now: --relaunch is missing, wrong or names no unresolved attempt
RESCHEDULE_REFUSED = 8  # reschedule: the job has launched on vanpi (deferred.zsh's own refusal)

DELIVERED = 'delivered'            # the result reached the phone
REPORT = 'report.txt'              # validated notification body awaiting delivery
RUN = 'remote-run.json'            # {run_id, token, launched}: a run exists on the Pi
LAUNCHING = 'launching.json'       # {token, at}: a launch was attempted, outcome unknown so far
HOLD = 'launch-unknown.json'       # {token, at, reason, detail}: the launch could not be reconciled
FAILURE = 'remote-failed.json'     # {run_id?, token, kind, reason}: the attempt ended badly
REPORT_CHECKS = 'report-checks.json'  # {run_id, count}: passes that found the finished run's report blank
HISTORY = 'runs.log'               # earlier attempts' run ids and abandoned tokens
FETCHED = 'prompt.fetched'         # the vanpi: prompt as read at launch
INLINE_PROMPT = 'prompt.txt'
STATE_RECORDS = (RUN, LAUNCHING, HOLD, FAILURE, REPORT_CHECKS)

RUN_ID = re.compile(r'^\d{4}-\d{6}-[0-9a-f]{4}(?:-[a-z0-9][a-z0-9-]*)?$')
TOKEN = re.compile(r'^d[0-9a-f]{16}$')  # survives pi_agent.py's 30-character name slug whole

# A run is collected until it is terminal or launch + --run-timeout + this;
# then the scheduler records a collection-deadline failure (deferred_pi_control expire).
COLLECT_GRACE = 7 * 86400
DEFAULT_RUN_TIMEOUT = 120  # minutes, add-agent's default --run-timeout


class RemoteState(str, Enum):
    UNLAUNCHED = 'unlaunched'
    LAUNCHING = 'launching'
    LAUNCH_UNKNOWN = 'launch-unknown'
    RUNNING = 'running'
    FINISHED = 'finished'
    FAILED = 'failed'


class HoldReason(str, Enum):
    """Why a lost launch could not be reconciled."""
    ABSENT = 'absent'                        # a covered listing has no run with the token
    AMBIGUOUS = 'ambiguous'                  # more than one run carries the token
    SEARCH_INCOMPLETE = 'search-incomplete'  # the listing did not reach back far enough


class FailureKind(str, Enum):
    """How an attempt ended badly."""
    REMOTE = 'remote'                            # the run died, exited nonzero or reported an error
    TIMED_OUT = 'timed-out'                      # stopped (and confirmed) after its --run-timeout
    NO_REPORT = 'no-report'                      # finished cleanly without a report
    COLLECTION_DEADLINE = 'collection-deadline'  # not collected by launch + run timeout + the grace


# Failures that prove the Pi run is over; any other unfinished state may still be active there.
CONFIRMED_TERMINAL = frozenset((FailureKind.REMOTE, FailureKind.TIMED_OUT, FailureKind.NO_REPORT))
# A remote result names the run whose status established it. Only the scheduler's collection
# deadline can end a launch whose run id was never learned (a held launch is its own record, HOLD).
TOKEN_ONLY_FAILURES = frozenset((FailureKind.COLLECTION_DEADLINE,))
OWED = frozenset((RemoteState.RUNNING, RemoteState.LAUNCHING, RemoteState.FINISHED))


class StateError(Exception):
    """A state record is unreadable, of another schema or contradicts the others."""


def scheduler_log():
    """The scheduler's durable log, when deferred.zsh named it (DEFERRED_LOG)."""
    value = os.environ.get('DEFERRED_LOG')
    return Path(value) if value else None


class PiJob:
    """One vanpi job directory's state files."""

    def __init__(self, path):
        self.path = Path(path)

    def exists(self, name):
        return (self.path / name).exists()

    def read(self, name):
        """The saved record, or None when absent."""
        try:
            raw = (self.path / name).read_text()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as error:
            raise StateError(f'{name} in {self.path} cannot be read ({error})') from None
        try:
            value = json.loads(raw)
        except ValueError:
            raise StateError(f'{name} in {self.path} is not valid JSON') from None
        if not isinstance(value, dict) or value.get('v') != SCHEMA:
            raise StateError(f'{name} in {self.path} is not a version-{SCHEMA} record')
        return value

    def write_bytes(self, name, data):
        temporary = self.path / f'.{name}.tmp'
        with open(temporary, 'wb') as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.path / name)

    def write(self, name, value):
        self.write_bytes(name, (json.dumps({'v': SCHEMA, **value}) + '\n').encode())

    def remove(self, *names):
        for name in names:
            (self.path / name).unlink(missing_ok=True)

    def append_history(self, line):
        """runs.log in the job (deleted with it) and the scheduler's durable log."""
        with open(self.path / HISTORY, 'a', encoding='utf-8') as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(f'{time.strftime("%Y-%m-%dT%H:%M")} {line}\n')
        log = scheduler_log()
        if log is not None:
            with open(log, 'a', encoding='utf-8') as output:
                output.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {self.path.name}: {line}\n')

    def recoverable_ids(self):
        """Run ids and tokens found anywhere in the state records, even damaged ones."""
        found = []
        for name in STATE_RECORDS:
            try:
                raw = (self.path / name).read_text(errors='replace')
            except OSError:
                continue
            for word in re.findall(r'[0-9a-z-]+', raw):
                if (RUN_ID.match(word) or TOKEN.match(word)) and word not in found:
                    found.append(word)
        return found


@dataclass(frozen=True)
class Snapshot:
    state: RemoteState
    run_id: str | None
    token: str | None
    since: int | None                  # when the attempt was launched (or its launch attempted)
    failure_kind: FailureKind | None

    @property
    def owes(self):
        """A collect-only pass is due at every check."""
        return self.state in OWED

    @property
    def unresolved(self):
        """The attempt is not known to be over on the Pi, so it may still be active there."""
        return (self.state in (RemoteState.RUNNING, RemoteState.LAUNCHING, RemoteState.LAUNCH_UNKNOWN)
                or self.failure_kind == FailureKind.COLLECTION_DEADLINE)

    @property
    def attempt_id(self):
        """The id `--relaunch` names for this attempt: its run id, else its launch token."""
        return self.run_id or self.token

    def lines(self):
        """key=value lines for deferred.zsh."""
        values = {'state': self.state.value, 'run': self.run_id or '', 'token': self.token or '',
                  'owes': int(self.owes), 'unresolved': int(self.unresolved),
                  'failure_kind': self.failure_kind.value if self.failure_kind else ''}
        return ''.join(f'{key}={value}\n' for key, value in values.items())


def _whole(record, key, name, minimum=0):
    value = record.get(key)
    if type(value) is not int or value < minimum:
        raise StateError(f'{name} holds no usable {key}')
    return value


def _text(record, key, name):
    if not isinstance(record.get(key), str):
        raise StateError(f'{name} holds no usable {key}')


def _ids(record, name, *, run=False, token=False):
    """The record's (run id, token): shapes checked, required ones present, and belonging together."""
    run_id, tok = record.get('run_id'), record.get('token')
    if run_id is not None and not (isinstance(run_id, str) and RUN_ID.match(run_id)):
        raise StateError(f'{name} holds a malformed run id')
    if tok is not None and not (isinstance(tok, str) and TOKEN.match(tok)):
        raise StateError(f'{name} holds a malformed token')
    if (run and run_id is None) or (token and tok is None) or (run_id is None and tok is None):
        raise StateError(f'{name} names no usable run id or token')
    if run_id and tok and not run_id.endswith('-' + tok):
        raise StateError(f'{name} holds a run id and a token of different launches')
    attempt = record.get('attempt')  # written by earlier versions; unused now
    if attempt is not None and (type(attempt) is not int or attempt < 0):
        raise StateError(f'{name} holds a malformed attempt number')
    return run_id, tok


def _enum(kind, record, key, name):
    try:
        return kind(record.get(key))
    except ValueError:
        raise StateError(f'{name} names an unknown {key}') from None


def _records(job):
    """Every state record, each checked on its own."""
    run, launching, hold, failure, checks = (job.read(name) for name in (RUN, LAUNCHING, HOLD, FAILURE, REPORT_CHECKS))
    if run is not None:
        _ids(run, RUN, run=True, token=True)
        _whole(run, 'launched', RUN)
    for record, name in ((launching, LAUNCHING), (hold, HOLD)):
        if record is not None:
            if _ids(record, name, token=True)[0] is not None:
                raise StateError(f'{name} names a run id')
            _whole(record, 'at', name)
    if hold is not None:
        _enum(HoldReason, hold, 'reason', HOLD)
        _text(hold, 'detail', HOLD)
    if failure is not None:
        kind = _enum(FailureKind, failure, 'kind', FAILURE)
        if _ids(failure, FAILURE)[0] is None and kind not in TOKEN_ONLY_FAILURES:
            raise StateError(f'{FAILURE} of kind {kind.value} names no run id')
        _text(failure, 'reason', FAILURE)
    if checks is not None:
        _ids(checks, REPORT_CHECKS, run=True)
        _whole(checks, 'count', REPORT_CHECKS, minimum=1)
    return run, launching, hold, failure, checks


def _consistent(job, run, launching, hold, failure, checks):
    """Only what a pass (or the scheduler) interrupted between two of its writes can leave."""
    report, delivered = job.exists(REPORT), job.exists(DELIVERED)
    if delivered and not report:
        raise StateError(f'{DELIVERED} without its {REPORT}')
    if report and run is None:
        raise StateError(f'{REPORT} without the run it reports ({RUN})')
    if checks is not None and (run is None or checks['run_id'] != run['run_id']):
        raise StateError(f'{REPORT_CHECKS} names a run other than the saved one')
    if hold is not None and (run is not None or failure is not None or report):
        raise StateError(f'{HOLD} beside a saved run, failure or report')
    if failure is not None and report:
        raise StateError(f'{FAILURE} beside a saved report')
    if failure is not None and failure.get('run_id') != (run and run['run_id']):
        raise StateError(f'{FAILURE} does not name the saved run')
    if failure is not None and run is not None and failure.get('token', run['token']) != run['token']:
        raise StateError(f'{FAILURE} names a token other than the saved run')
    # A launch marker is cleared right after the record that replaces it is saved.
    later = run or hold or failure
    if launching is not None and later is not None and later.get('token') != launching['token']:
        raise StateError(f'{LAUNCHING} names a token other than the later records')
    return report or delivered


def snapshot(job: PiJob) -> Snapshot:
    """Where the job's Pi run stands; StateError when that cannot be told."""
    run, launching, hold, failure, checks = _records(job)
    finished = _consistent(job, run, launching, hold, failure, checks)
    if finished:
        return Snapshot(RemoteState.FINISHED, run['run_id'], run['token'], run['launched'], None)
    if failure is not None:
        token = failure.get('token') or (run and run['token'])
        since = run['launched'] if run else launching and launching['at']
        return Snapshot(RemoteState.FAILED, failure.get('run_id'), token, since, FailureKind(failure['kind']))
    if hold is not None:
        return Snapshot(RemoteState.LAUNCH_UNKNOWN, None, hold['token'], hold['at'], None)
    if run is not None:
        return Snapshot(RemoteState.RUNNING, run['run_id'], run['token'], run['launched'], None)
    if launching is not None:
        return Snapshot(RemoteState.LAUNCHING, None, launching['token'], launching['at'], None)
    return Snapshot(RemoteState.UNLAUNCHED, None, None, None, None)
