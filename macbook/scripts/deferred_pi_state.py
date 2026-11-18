"""Durable state of a `deferred.zsh add-agent --host vanpi` job, and its trusted reader.

One file per fact in the job directory, each written atomically; their
presence is the state. The job's own `run` (a copy of deferred_pi_run.py)
writes them during its passes. deferred.zsh and the list assembler read them
through `snapshot()` from the repository's copy of this module, so listing,
scheduling and cancelling never execute anything the job directory holds.

JSON records carry `v` (SCHEMA); a record of another version, malformed JSON
or contradictory fields make the state unreadable (StateError), which the
listing shows as an invalid row and cancel refuses unless `--abandon`.
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
RELAUNCH_REFUSED = 7  # retry/run-now: the held launch's token was not given (deferred.zsh relays it)

DELIVERED = 'delivered'            # the result reached the phone
REPORT = 'report.txt'              # validated notification body awaiting delivery
RUN = 'remote-run.json'            # {run_id, token, launched, attempt}: a run exists on the Pi
LAUNCHING = 'launching.json'       # {token, at, attempt}: a launch was attempted, outcome unknown so far
HOLD = 'launch-unknown.json'       # {token, at, attempt, reason, detail}: the launch could not be reconciled
FAILURE = 'remote-failed.json'     # {run_id, token, attempt, kind, reason}: the attempt's run ended badly
REPORT_CHECKS = 'report-checks.json'  # {run_id, count}: passes that found the finished run's report blank
HISTORY = 'runs.log'               # earlier attempts' run ids and abandoned tokens
FETCHED = 'prompt.fetched'         # the vanpi: prompt as read at launch
INLINE_PROMPT = 'prompt.txt'
STATE_RECORDS = (RUN, LAUNCHING, HOLD, FAILURE, REPORT_CHECKS)

RUN_ID = re.compile(r'^\d{4}-\d{6}-[0-9a-f]{4}(?:-[a-z0-9][a-z0-9-]*)?$')
TOKEN = re.compile(r'^d[0-9a-f]{16}$')  # survives pi_agent.py's 30-character name slug whole


class RemoteState(str, Enum):
    UNLAUNCHED = 'unlaunched'
    LAUNCHING = 'launching'
    LAUNCH_UNKNOWN = 'launch-unknown'
    RUNNING = 'running'
    FINISHED = 'finished'
    FAILED = 'failed'


class HoldReason(str, Enum):
    """Why a lost launch could not be reconciled."""
    ABSENT = 'absent'                        # a complete listing has no run with the token
    AMBIGUOUS = 'ambiguous'                  # more than one run carries the token
    SEARCH_INCOMPLETE = 'search-incomplete'  # the listing did not reach back far enough


class FailureKind(str, Enum):
    """How an attempt's Pi run ended badly."""
    REMOTE = 'remote'                            # the run died, exited nonzero or reported an error
    TIMED_OUT = 'timed-out'                      # stopped (and confirmed) after its --run-timeout
    NO_REPORT = 'no-report'                      # finished without a report; never relaunched automatically
    COLLECTION_DEADLINE = 'collection-deadline'  # not collected by launch + run timeout + the grace


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
    owes: bool                       # a collect-only pass is due at every check
    failure_attempt: int | None
    failure_kind: FailureKind | None

    def lines(self):
        """key=value lines for deferred.zsh."""
        values = {'state': self.state.value, 'run': self.run_id or '', 'token': self.token or '',
                  'owes': int(self.owes), 'failure_attempt': '' if self.failure_attempt is None else self.failure_attempt,
                  'failure_kind': self.failure_kind.value if self.failure_kind else ''}
        return ''.join(f'{key}={value}\n' for key, value in values.items())


def _checked(record, name, *, run_id=False, token=False, either=False):
    """The record's run id and token after checking their shapes."""
    run, tok = record.get('run_id'), record.get('token')
    run_ok = isinstance(run, str) and bool(RUN_ID.match(run))
    tok_ok = isinstance(tok, str) and bool(TOKEN.match(tok))
    if (run_id and not run_ok) or (token and not tok_ok) or (either and not (run_ok or tok_ok)):
        raise StateError(f'{name} names no usable run id or token')
    if (run is not None and not run_ok) or (tok is not None and not tok_ok):
        raise StateError(f'{name} holds a malformed run id or token')
    attempt = record.get('attempt')
    if attempt is not None and (type(attempt) is not int or attempt < 0):
        raise StateError(f'{name} holds a malformed attempt number')
    return (run if run_ok else None), (tok if tok_ok else None)


def snapshot(job: PiJob) -> Snapshot:
    """Where the job's Pi run stands; StateError when that cannot be told."""
    records = {name: job.read(name) for name in (RUN, LAUNCHING, HOLD, FAILURE)}
    finished = job.exists(DELIVERED) or job.exists(REPORT)
    run = records[RUN] and _checked(records[RUN], RUN, run_id=True, token=True)
    if records[FAILURE] is not None:
        failure = records[FAILURE]
        try:
            kind = FailureKind(failure.get('kind'))
        except ValueError:
            raise StateError(f'{FAILURE} names an unknown failure kind') from None
        run_id, token = _checked(failure, FAILURE, either=True)
        return Snapshot(RemoteState.FINISHED if finished else RemoteState.FAILED, run_id, token,
                        finished, failure.get('attempt'), kind)
    if finished:
        return Snapshot(RemoteState.FINISHED, run[0] if run else None, run[1] if run else None, True, None, None)
    if records[HOLD] is not None:
        _, token = _checked(records[HOLD], HOLD, token=True)
        try:
            HoldReason(records[HOLD].get('reason'))
        except ValueError:
            raise StateError(f'{HOLD} names an unknown reason') from None
        return Snapshot(RemoteState.LAUNCH_UNKNOWN, None, token, False, None, None)
    if run:
        return Snapshot(RemoteState.RUNNING, run[0], run[1], True, None, None)
    if records[LAUNCHING] is not None:
        _, token = _checked(records[LAUNCHING], LAUNCHING, token=True)
        return Snapshot(RemoteState.LAUNCHING, None, token, True, None, None)
    return Snapshot(RemoteState.UNLAUNCHED, None, None, False, None, None)
