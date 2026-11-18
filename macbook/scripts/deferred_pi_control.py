#!/usr/bin/python3 -IB
"""deferred.zsh's verbs on a vanpi agent job, run from the repository.

Nothing here executes a file from the job directory: the state is read as data
through deferred_pi_state, and the Pi is reached through the installed
pi_agent.py, so a damaged or missing job `run` cannot change what these say.

  describe DIR                    key=value lines of the Pi run state; exit 1 when unreadable
  cancel [--abandon|--untrusted] DIR
                                  exit 0 when the job may be deleted, 6 while a run may be active;
                                  --untrusted (a job record that cannot be trusted) refuses
                                  unless --abandon is given instead
  rearm DIR VERB [ID]             retry/run-now: exit 0 when VERB may go ahead (an ended attempt is
                                  cleared); 7 when the previous attempt is unresolved and ID does
                                  not name it, or ID names nothing unresolved
  expire DIR [MINUTES]            exit 0 after recording a collection-deadline failure for a run
                                  owed past launch + MINUTES (the job's --run-timeout, default
                                  DEFAULT_RUN_TIMEOUT) + the grace; 1 otherwise
"""
from __future__ import annotations

from pathlib import Path
import sys
import time

# -I keeps the script's own folder off sys.path; the modules it needs sit beside it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from deferred_pi_state import (  # noqa: E402
    COLLECT_GRACE, DEFAULT_RUN_TIMEOUT, DELIVERED, DONE, FAILED, FAILURE, FETCHED, HOLD, LAUNCHING, REFUSED, RELAUNCH_REFUSED, REPORT,
    REPORT_CHECKS, RUN, TOKEN, USAGE, FailureKind, PiJob, RemoteState, StateError, snapshot)
import deferred_pi_remote as remote  # noqa: E402

CANCEL_BUDGET = 180  # seconds for one cancel; the Mac Panel waits 200 s on it
# Removal order when an attempt is cleared: each step leaves a state snapshot() accepts,
# at worst one that still owes a collection, never one that looks resolved.
CLEARED = (DELIVERED, REPORT, FAILURE, HOLD, REPORT_CHECKS, RUN, LAUNCHING, FETCHED)


class Refused(Exception):
    """cancel cannot show that no Pi run is left active."""


def describe(job):
    try:
        print(snapshot(job).lines(), end='')
    except StateError as error:
        print(error, file=sys.stderr)
        return FAILED
    return DONE


def recovery(ids):
    return '\n'.join(remote.launcher_commands(token=i) if TOKEN.match(i) else remote.launcher_commands(run_id=i)
                     for i in ids)


def cancel(job, abandon, untrusted=False):
    """Exit 0 when the job may be deleted; REFUSED while a Pi run may still be active."""
    try:
        if untrusted:
            raise StateError('its job record cannot be trusted')
        snap = snapshot(job)
    except StateError as error:
        found = job.recoverable_ids()
        named = ', '.join(found) or 'none recoverable'
        if abandon:
            job.append_history(f'abandoned by cancel --abandon; Pi state not trusted ({error}); ids: {named}')
            print(f'Abandoned the job; {error}. Ids found: {named}.\n{recovery(found)}')
            return DONE
        print(f'The Pi run state is not trusted ({error}), so whether a Pi run is still active cannot be '
              f'checked; the job is kept. Ids found: {named}.\n{recovery(found)}\n'
              'cancel --abandon JOB deletes the job anyway.', file=sys.stderr)
        return REFUSED
    if not snap.unresolved:
        return DONE
    run_id, token = snap.run_id, snap.token
    if abandon:
        what = f'run {run_id}' if run_id else f'the launch with token {token}'
        job.append_history(f'{what} abandoned by cancel --abandon; it may still be active')
        print(f'Abandoned {what}; it may still be active on vanpi.\n'
              f'{remote.launcher_commands(run_id=run_id, token=token)}')
        return DONE
    try:
        run_id = stop_and_confirm(run_id, token, remote.Budget(CANCEL_BUDGET))
    except (Refused, remote.BudgetSpent) as reason:
        print(f'{reason} The job is kept so its run is not left on vanpi unseen.\n'
              f'{remote.launcher_commands(run_id=run_id, token=token)}\n'
              'cancel --abandon JOB deletes the job anyway.', file=sys.stderr)
        return REFUSED
    print(f'Run {run_id} on vanpi is not running.')
    return DONE


def stop_and_confirm(run_id, token, budget):
    """Finds the run (by token when no id was saved), stops it if running; its id once confirmed."""
    if not run_id:
        search = remote.runs_with_token(token, budget, remote.STATUS_TIMEOUT // 2)
        if search is None:
            raise Refused(f'vanpi could not be listed to look for token {token}.')
        if not search.found:
            raise Refused(f'{len(search.matches) or "No"} runs on vanpi match token {token}'
                          f'{"" if search.covered else " (search incomplete)"}, so it is unknown '
                          'whether that launch is running.')
        run_id = search.found
    status = remote.run_status(run_id, budget, remote.STATUS_TIMEOUT // 2)
    if status is None:
        raise Refused(f'Could not read run {run_id} on vanpi.')
    if status.state == 'running' and remote.stop_confirmed(run_id, budget, remote.STATUS_TIMEOUT // 2) is None:
        raise Refused(f'Run {run_id} on vanpi was sent a stop but still shows running (or could not be read).')
    return run_id


def clear_attempt(job, line):
    job.append_history(line)
    job.remove(*CLEARED)


def rearm(job, verb, given):
    """retry/run-now: whether VERB may go ahead, and the ended attempt cleared so it can.

    A confirmed-terminal attempt needs no consent. An unresolved one (it may
    still be active on vanpi) needs GIVEN naming exactly that attempt, as
    `list` shows it; run-now without one collects a run the job still owes.
    """
    name = job.path.name
    try:
        snap = snapshot(job)
        unresolved, ids, problem = snap.unresolved, [i for i in (snap.run_id, snap.token) if i], None
    except StateError as error:
        snap, unresolved, ids, problem = None, True, job.recoverable_ids(), str(error)
    if not unresolved:
        if given:
            print(f'{name}: nothing on vanpi is unresolved, so --relaunch {given} names no current attempt '
                  f'(it was already replaced or is over); plain `{verb} {name}` is enough.', file=sys.stderr)
            return RELAUNCH_REFUSED
        if snap.state == RemoteState.FAILED:
            clear_attempt(job, f'attempt {snap.attempt_id} ended ({snap.failure_kind.value}); cleared by {verb}')
        return DONE
    if given is None and verb == 'run-now' and snap is not None and snap.owes:
        return DONE  # run-now collects the run the job owes; it starts nothing
    if given is None or given not in ids:
        what = f'its Pi state is unreadable ({problem})' if problem else \
            f'its Pi attempt {snap.attempt_id} is {snap.failure_kind.value if snap.failure_kind else snap.state.value}'
        named = ' or '.join(ids) or '(no id recoverable; cancel --abandon is the way out)'
        wrong = f'--relaunch {given} does not name it. ' if given else ''
        print(f'{name}: {what} and may still be active on vanpi. {wrong}To start another run anyway: '
              f'deferred.zsh {verb} {name} --relaunch {named}', file=sys.stderr)
        return RELAUNCH_REFUSED
    clear_attempt(job, f'attempt {given} abandoned by {verb} --relaunch; it may still be active on vanpi')
    print(f'Cleared the unresolved attempt {given}; the next launch starts a new run.')
    return DONE


def expire(job, minutes):
    """The scheduler's collection deadline, enforced from the state record alone."""
    try:
        snap = snapshot(job)
    except StateError:
        return FAILED
    if snap.state not in (RemoteState.RUNNING, RemoteState.LAUNCHING) or snap.since is None:
        return FAILED
    if time.time() <= snap.since + minutes * 60 + COLLECT_GRACE:
        return FAILED
    what = f'run {snap.run_id}' if snap.run_id else f'the launch with token {snap.token}'
    reason = (f'{what} was not collected by its collection deadline (launch + {minutes} min + '
              f'{COLLECT_GRACE // 86400} days); it may still be active')
    record = {'run_id': snap.run_id, 'token': snap.token} if snap.run_id else {'token': snap.token}
    job.write(FAILURE, {**record, 'kind': FailureKind.COLLECTION_DEADLINE.value, 'reason': reason})
    if not snap.run_id:
        job.remove(LAUNCHING)
    print(f'{reason[0].upper()}{reason[1:]}.\n{remote.launcher_commands(run_id=snap.run_id, token=snap.token)}')
    return DONE


def main(argv):
    if len(argv) == 2 and argv[0] == 'describe':
        return describe(PiJob(argv[1]))
    if len(argv) in (2, 3) and argv[0] == 'cancel' and argv[1:-1] in ([], ['--abandon'], ['--untrusted']):
        return cancel(PiJob(argv[-1]), abandon=argv[1] == '--abandon', untrusted=argv[1] == '--untrusted')
    if len(argv) in (3, 4) and argv[0] == 'rearm' and argv[2] in ('retry', 'run-now'):
        return rearm(PiJob(argv[1]), argv[2], argv[3] if len(argv) == 4 else None)
    if len(argv) in (2, 3) and argv[0] == 'expire' and argv[2:] in ([], [m for m in argv[2:] if m.isdigit()]):
        return expire(PiJob(argv[1]), int(argv[2]) if len(argv) == 3 else DEFAULT_RUN_TIMEOUT)
    print(__doc__.split('\n\n')[-1], file=sys.stderr)
    return USAGE


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
