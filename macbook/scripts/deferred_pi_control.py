#!/usr/bin/python3 -IB
"""deferred.zsh's verbs on a vanpi agent job, run from the repository.

Nothing here executes a file from the job directory: the state is read as data
through deferred_pi_state, and the Pi is reached through the installed
pi_agent.py, so a damaged or missing job `run` cannot change what these say.

  describe DIR                 key=value lines of the Pi run state; exit 1 when unreadable
  cancel [--abandon] DIR       exit 0 when the job may be deleted, 6 while a run may be active
  clear-hold DIR TOKEN VERB    retry/run-now: clears a launch-unknown hold only for its own
                               token (TOKEN "-" when none was given); exit 7 otherwise
"""
from __future__ import annotations

from pathlib import Path
import sys

# -I keeps the script's own folder off sys.path; the modules it needs sit beside it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from deferred_pi_state import (  # noqa: E402
    DONE, FAILED, HOLD, REFUSED, RELAUNCH_REFUSED, USAGE, FailureKind, PiJob, RemoteState, StateError,
    TOKEN, snapshot)
import deferred_pi_remote as remote  # noqa: E402

CANCEL_BUDGET = 180  # seconds for one cancel; the Mac Panel waits 200 s on it
ACTIVE = (RemoteState.RUNNING, RemoteState.LAUNCHING, RemoteState.LAUNCH_UNKNOWN)


class Refused(Exception):
    """cancel cannot show that no Pi run is left active."""


def describe(job):
    try:
        print(snapshot(job).lines(), end='')
    except StateError as error:
        print(error, file=sys.stderr)
        return FAILED
    return DONE


def possibly_active(snap):
    return snap.state in ACTIVE or snap.failure_kind == FailureKind.COLLECTION_DEADLINE


def recovery(ids):
    return '\n'.join(remote.launcher_commands(token=i) if TOKEN.match(i) else remote.launcher_commands(run_id=i)
                     for i in ids)


def cancel(job, abandon):
    """Exit 0 when the job may be deleted; REFUSED while a Pi run may still be active."""
    try:
        snap = snapshot(job)
    except StateError as error:
        found = job.recoverable_ids()
        named = ', '.join(found) or 'none recoverable'
        if abandon:
            job.append_history(f'abandoned by cancel --abandon; Pi state unreadable ({error}); ids: {named}')
            print(f'Abandoned the job; its Pi run state is unreadable ({error}). Ids found: {named}.\n{recovery(found)}')
            return DONE
        print(f'{error}, so whether a Pi run is still active cannot be checked; the job is kept. '
              f'Ids found: {named}.\n{recovery(found)}\ncancel --abandon JOB deletes the job anyway.', file=sys.stderr)
        return REFUSED
    if not possibly_active(snap):
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
        if len(search.matches) != 1:
            raise Refused(f'{len(search.matches) or "No"} runs on vanpi match token {token}'
                          f'{"" if search.complete else " (search incomplete)"}, so it is unknown '
                          'whether that launch is running.')
        run_id = search.matches[0]
    status = remote.run_status(run_id, budget, remote.STATUS_TIMEOUT // 2)
    if status is None:
        raise Refused(f'Could not read run {run_id} on vanpi.')
    if status.state == 'running' and remote.stop_confirmed(run_id, budget, remote.STATUS_TIMEOUT // 2) is None:
        raise Refused(f'Run {run_id} on vanpi was sent a stop but still shows running (or could not be read).')
    return run_id


def clear_hold(job, token, verb):
    """The user's consent to start another run, valid only for the held launch it names."""
    try:
        snap = snapshot(job)
    except StateError as error:
        print(f'{error}; the Pi run state must be readable before another run starts.', file=sys.stderr)
        return FAILED
    if snap.state != RemoteState.LAUNCH_UNKNOWN:
        return DONE
    if token != snap.token:
        print(f'{job.path.name}: its Pi launch with token {snap.token} is unknown and may still be running. '
              f'To start another run anyway: deferred.zsh {verb} {job.path.name} --relaunch {snap.token}',
              file=sys.stderr)
        return RELAUNCH_REFUSED
    hold = job.read(HOLD)
    job.append_history(f'launch token {token} abandoned by {verb} ({hold.get("detail") or hold.get("reason")}; '
                       'that launch may still be active)')
    job.remove(HOLD)
    print(f'Cleared the unknown launch {token}; the next attempt starts a new run.')
    return DONE


def main(argv):
    if len(argv) == 2 and argv[0] == 'describe':
        return describe(PiJob(argv[1]))
    if len(argv) in (2, 3) and argv[0] == 'cancel' and argv[1:-1] in ([], ['--abandon']):
        return cancel(PiJob(argv[-1]), abandon=len(argv) == 3)
    if len(argv) == 4 and argv[0] == 'clear-hold' and argv[3] in ('retry', 'run-now'):
        return clear_hold(PiJob(argv[1]), argv[2], argv[3])
    print(__doc__.split('\n\n')[-1], file=sys.stderr)
    return USAGE


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
