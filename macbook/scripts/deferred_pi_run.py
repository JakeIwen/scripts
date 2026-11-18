#!/usr/bin/python3 -IB
"""Run script of a job queued by `deferred.zsh add-agent --host vanpi`.

Copied into the job directory as `run`, beside the job's agent.env and its
copies of deferred_pi_state.py and deferred_pi_remote.py, so the job stands
alone. Each pass (deferred.zsh runs it with the job directory as the working
directory) runs the saved prompt on vanpi through pi_agent.py and sends the
agent's final message to the phone. A Pi run outlives the pass that started
it, so every pass first dispatches on the durable state files
(deferred_pi_state); only a job with no launch on record evaluates --ready or
reads the prompt. Exit 75 means "not finished yet" to deferred.zsh: no attempt
spent.

`run` launches when nothing is on record; `run collect` is the collect-only
pass deferred.zsh makes for a job that owns a Pi run, whatever its state, due
time or give-up time: it never launches.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

JOB = Path(__file__).resolve().parent
# -I keeps the script's own folder off sys.path; the modules it needs sit beside it.
sys.path.insert(0, str(JOB))
from deferred_pi_state import (  # noqa: E402
    DELIVERED, DONE, FAILED, FAILURE, FETCHED, HOLD, INLINE_PROMPT, LAUNCHING, NOT_READY, REPORT,
    REPORT_CHECKS, RUN, RUN_ID, USAGE, FailureKind, HoldReason, PiJob, StateError)
import deferred_pi_remote as remote  # noqa: E402

PROMPT_LIMIT = 256 * 1024
SUMMARY_LIMIT = 600
POLL_WINDOW, POLL_INTERVAL = 240, 20
# Seconds of the job's --timeout kept for launching, stopping and the final
# status read; only the rest is spent polling a running run.
POLL_RESERVE, PASS_MARGIN = 600, 60
# What a launch needs after --ready: the reachability probe, the vanpi: prompt
# read, the launch call and one status read. deferred.zsh add-agent refuses a
# vanpi --timeout below MIN_PASS_TIMEOUT (it keeps its own copy of the number).
LAUNCH_NEED = 2 * remote.SSH_TIMEOUT + remote.LAUNCH_TIMEOUT + remote.STATUS_TIMEOUT
MIN_PASS_TIMEOUT = PASS_MARGIN + LAUNCH_NEED
# A run is collected until it is terminal or launch + --run-timeout + this;
# then the job fails with the commands to find and stop the run by hand.
COLLECT_GRACE = 7 * 86400
REPORT_CHECK_LIMIT = 3  # passes that may find a finished run's report blank before it fails

SETTINGS = ('agent_name', 'agent_model', 'agent_effort', 'agent_cwd', 'agent_ready',
            'agent_pi_prompt', 'agent_run_timeout', 'agent_notify')


class NotReady(Exception):
    """Nothing is wrong with the job yet; the next pass tries again (exit 75)."""


class Failure(Exception):
    """An ordinary failure of this attempt (exit 1)."""


# ---- the Pi's prompt reader: a fixed program; the path arrives as data on stdin ----

REMOTE_READER = r'''
import os, stat, sys
LIMIT = %d
def fail(message):
    sys.stderr.write(message + "\n")
    sys.exit(3)
raw = sys.stdin.buffer.read()
try:
    path = raw.decode("utf-8")
except UnicodeDecodeError:
    fail("the prompt path is not UTF-8")
if not path.startswith("/") or "\0" in path or "\n" in path:
    fail("the prompt path must be one absolute path")
try:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
except OSError as error:
    fail("cannot open %%s: %%s" %% (path, error.strerror))
info = os.fstat(fd)
if not stat.S_ISREG(info.st_mode):
    fail("%%s is not a regular file" %% path)
data = b""
while len(data) <= LIMIT:
    chunk = os.read(fd, 65536)
    if not chunk:
        break
    data += chunk
if len(data) > LIMIT:
    fail("%%s is larger than %%d bytes" %% (path, LIMIT))
try:
    text = data.decode("utf-8")
except UnicodeDecodeError:
    fail("%%s is not UTF-8 text" %% path)
if not text.strip():
    fail("%%s is blank" %% path)
sys.stdout.buffer.write(data)
''' % PROMPT_LIMIT
REMOTE_READER_COMMAND = "python3 -I -c 'import base64;exec(base64.b64decode(\"%s\"))'" % (
    base64.b64encode(REMOTE_READER.encode()).decode())



# ---- the job's own files ----

def job_value(key, default):
    """A whole-number field of the scheduler's job record (timeout, attempts)."""
    try:
        for line in (JOB / 'job').read_text().splitlines():
            if line.startswith(key + '=') and line[len(key) + 1:].isdigit():
                return int(line[len(key) + 1:])
    except OSError:
        pass
    return default


def load_settings():
    """agent.env is zsh-quoted; zsh decodes it and hands each value back NUL-terminated."""
    script = 'source "$1/agent.env" || exit 1; shift; for k; do print -rn -- "${(P)k-}"; print -rn -- $\'\\0\'; done'
    result = subprocess.run(['/bin/zsh', '-f', '-c', script, 'settings', str(JOB), *SETTINGS],
                            capture_output=True, timeout=30)
    if result.returncode != 0:
        raise Failure(f'cannot read {JOB}/agent.env')
    values = result.stdout.decode('utf-8', 'surrogateescape').split('\0')[:-1]
    return dict(zip(SETTINGS, values))


def run_timeout_seconds(settings):
    return int(settings['agent_run_timeout'] or 120) * 60


# ---- one pass ----

class PassClock:
    """The pass's own budget: every remote call and all polling end inside --timeout."""

    def __init__(self, timeout):
        self.timeout = timeout
        self.budget = remote.Budget(timeout - PASS_MARGIN)

    def may_poll(self, collecting_since):
        window = min(POLL_WINDOW, max(0, self.timeout - POLL_RESERVE))
        return (time.monotonic() + POLL_INTERVAL <= collecting_since + window
                and self.budget.remaining() >= POLL_INTERVAL + remote.STATUS_TIMEOUT)


def run_pass(collect_only):
    os.environ['PATH'] = f'{Path.home()}/.local/bin:{os.environ.get("PATH", "/usr/bin:/bin")}'
    try:
        return dispatch(PiJob(JOB), PassClock(job_value('timeout', 900)), collect_only)
    except remote.BudgetSpent as reason:
        print(f'This pass stopped early: {reason}; the next check carries on from the saved state.')
        return NOT_READY
    except NotReady as reason:
        print(reason)
        return NOT_READY
    except (Failure, StateError) as reason:
        print(reason)
        return FAILED


def dispatch(job, clock, collect_only):
    if job.exists(DELIVERED):
        return DONE
    settings = load_settings()
    if job.exists(REPORT):
        return deliver(job, settings)
    hold = job.read(HOLD)
    if hold is not None:
        raise Failure(held(hold.get('token'), hold.get('detail') or hold.get('reason')))
    failure = job.read(FAILURE)
    if failure is not None:
        if collect_only:
            raise Failure(f'This attempt already ended: {failure.get("reason")}.')
        # deferred.zsh authorized a new attempt; the failed run is history and never looked at again.
        what = failure.get('run_id') or f'with token {failure.get("token")}'
        job.append_history(f'run {what} ended ({failure.get("kind")}: {failure.get("reason")}); '
                           'superseded by a new attempt')
        job.remove(FAILURE, RUN, LAUNCHING, FETCHED, REPORT_CHECKS)
    run = job.read(RUN)
    if run is not None:
        job.remove(LAUNCHING)  # a crash between saving the id and clearing the marker
        return collect(job, settings, run, clock)
    launching = job.read(LAUNCHING)
    if launching is not None:
        return reconcile(job, settings, launching, clock)
    if collect_only:
        raise NotReady('Nothing on vanpi to collect for this job.')
    return launch(job, settings, clock)


def ready_now(settings, budget):
    """Evaluates --ready inside the time the pass still needs to launch afterwards."""
    ready = settings['agent_ready']
    room = budget.remaining() - LAUNCH_NEED
    if room < remote.MIN_CALL:
        raise NotReady('Too little of this pass is left to launch; the next check tries again.')
    if not ready:
        return
    try:
        result = subprocess.run(['/bin/zsh', '-c', ready], timeout=room)
    except subprocess.TimeoutExpired:
        raise NotReady('Not ready: the --ready command did not finish in the time this pass '
                       'has before it must launch.') from None
    if result.returncode != 0:
        raise NotReady('Not ready: the --ready command exited nonzero.')


def launch(job, settings, clock):
    budget = clock.budget
    ready_now(settings, budget)
    prompt = acquire_prompt(job, settings, budget)
    reachable = remote.ssh('true', budget)
    if reachable is None or reachable.returncode != 0:
        raise NotReady('vanpi did not answer; the launch waits for the next check.')
    if budget.remaining() < remote.LAUNCH_TIMEOUT + remote.STATUS_TIMEOUT:
        raise NotReady('Too little of this pass is left to launch and read the run once; '
                       'the next check launches.')
    token = 'd' + secrets.token_hex(8)  # survives pi_agent.py's 30-character name slug whole
    at, attempt = int(time.time()), job_value('attempts', 0)
    job.write(LAUNCHING, {'token': token, 'at': at, 'attempt': attempt})
    result = remote.launcher(['run', '--agent', settings['agent_model'], '--effort', settings['agent_effort'],
                              '--cd', settings['agent_cwd'], '--name', token, '--no-wait',
                              '--prompt-file', str(prompt)], budget, remote.LAUNCH_TIMEOUT)
    if result is not None and result.returncode == remote.LAUNCHER_USAGE:
        job.remove(LAUNCHING)
        raise Failure(f'pi_agent.py refused the launch:\n{remote.text(result)}')
    match = remote.STARTED.search(result.stdout.decode('utf-8', 'replace')) if result and result.returncode == 0 else None
    run_id = match.group(1) if match else None
    if not run_id or not RUN_ID.match(run_id) or not run_id.endswith('-' + token):
        raise NotReady(f'The launch outcome is unknown ({remote.text(result) or "no answer"}); a later '
                       f'check looks for token {token} on vanpi before doing anything else.\n'
                       f'{remote.launcher_commands(token=token)}')
    save_run(job, run_id, token, at, attempt)
    print(f'Started {run_id} on vanpi.')
    return collect(job, settings, job.read(RUN), clock)


def acquire_prompt(job, settings, budget):
    """A local file holding the prompt to send; vanpi: prompts are read from the Pi now."""
    path = settings['agent_pi_prompt']
    if not path:
        try:
            data = (JOB / INLINE_PROMPT).read_bytes()
        except OSError as error:
            raise Failure(f'Cannot read the saved prompt: {error.strerror}') from None
        return checked_prompt(data, JOB / INLINE_PROMPT)
    result = remote.ssh(REMOTE_READER_COMMAND, budget, path.encode('utf-8', 'surrogateescape'))
    if result is None or result.returncode == remote.SSH_UNREACHABLE:
        raise NotReady('vanpi did not answer; the prompt is read at the next check.')
    if result.returncode != 0:
        raise Failure(f'The prompt file vanpi:{path} cannot be used: '
                      f'{result.stderr.decode("utf-8", "replace").strip() or "exit " + str(result.returncode)}')
    job.write_bytes(FETCHED, result.stdout)
    return checked_prompt(result.stdout, JOB / FETCHED)


def checked_prompt(data, path):
    if len(data) > PROMPT_LIMIT:
        raise Failure(f'The prompt is larger than {PROMPT_LIMIT} bytes.')
    try:
        prompt = data.decode('utf-8')
    except UnicodeDecodeError:
        raise Failure('The prompt is not UTF-8 text.') from None
    if not prompt.strip():
        raise Failure('The prompt is blank.')
    return path


def save_run(job, run_id, token, launched, attempt):
    job.write(RUN, {'run_id': run_id, 'token': token, 'launched': launched, 'attempt': attempt})
    job.remove(LAUNCHING)


def collection_over(launched, settings):
    """True once launch + --run-timeout + COLLECT_GRACE has passed."""
    return time.time() > int(launched or 0) + run_timeout_seconds(settings) + COLLECT_GRACE


def reconcile(job, settings, launching, clock):
    """A launch whose answer was lost: adopt its run by token, never launch again."""
    token = launching.get('token')
    search = remote.runs_with_token(token, clock.budget)
    if search is None:
        if collection_over(launching.get('at'), settings):
            record_failure(job, launching, FailureKind.COLLECTION_DEADLINE,
                           'its lost launch could not be looked up before the collection deadline')
        raise NotReady(f'vanpi could not be listed; token {token} is looked for at the next check.\n'
                       f'{remote.launcher_commands(token=token)}')
    if len(search.matches) == 1:
        save_run(job, search.matches[0], token, launching.get('at') or int(time.time()), launching.get('attempt'))
        print(f'Found the run of token {token}: {search.matches[0]}.')
        return collect(job, settings, job.read(RUN), clock)
    if search.matches:
        reason, detail = HoldReason.AMBIGUOUS, f'{len(search.matches)} runs match'
    elif search.complete:
        reason, detail = HoldReason.ABSENT, 'no run with that token'
    else:
        reason, detail = (HoldReason.SEARCH_INCOMPLETE, f'search incomplete: the newest '
                          f'{remote.LIST_LIMITS[-1]} runs on vanpi do not include it and older ones were not listed')
    job.write(HOLD, {'token': token, 'at': launching.get('at'), 'attempt': launching.get('attempt'),
                     'reason': reason.value, 'detail': detail})
    job.remove(LAUNCHING)
    raise Failure(held(token, detail))


def held(token, detail):
    return (f'Held: the launch with token {token} is unknown ({detail}); it may be running on vanpi, '
            f'so no check launches it again.\n{remote.launcher_commands(token=token)}\n'
            f'Another run needs that token: deferred.zsh retry {JOB.name} --relaunch {token} '
            f'(or run-now); cancel --abandon {JOB.name} drops the job.')


def collect(job, settings, run, clock):
    run_id = run.get('run_id')
    deadline = int(run.get('launched') or 0) + run_timeout_seconds(settings)
    since = time.monotonic()
    status = remote.run_status(run_id, clock.budget)
    while status is not None and status.state == 'running' and time.time() < deadline and clock.may_poll(since):
        time.sleep(POLL_INTERVAL)
        status = remote.run_status(run_id, clock.budget)
    if status is None:
        if collection_over(run.get('launched'), settings):
            record_failure(job, run, FailureKind.COLLECTION_DEADLINE,
                           'it could not be read on vanpi before the collection deadline; it may still be active')
        raise NotReady(f'Could not read run {run_id} on vanpi; the next check tries again.\n'
                       f'{remote.launcher_commands(run_id=run_id)}')
    if status.state == 'running' and time.time() < deadline:
        raise NotReady(f'{remote.launcher_commands(run_id=run_id)}\n'
                       f'Pi run {run_id} is still running on vanpi; a later check collects it.')
    if status.state == 'running':
        status = remote.stop_confirmed(run_id, clock.budget)
        if status is None:
            if collection_over(run.get('launched'), settings):
                record_failure(job, run, FailureKind.COLLECTION_DEADLINE,
                               'its stop after --run-timeout was never confirmed; it may still be active')
            raise NotReady(f'Run {run_id} passed its --run-timeout; the stop is not confirmed yet '
                           f'and is tried again at the next check.\n{remote.launcher_commands(run_id=run_id)}')
        if not status.succeeded:
            record_failure(job, run, FailureKind.TIMED_OUT, 'timed out: stopped after its --run-timeout', status)
    return settle(job, settings, run, status)


def settle(job, settings, run, status):
    run_id = run.get('run_id')
    if status.clean_finish and status.report_blank:
        return blank_report(job, run, status)
    if not status.succeeded:
        if status.state == 'died':
            reason = 'died without finishing (Pi reboot or kill)'
        elif status.error:
            reason = f'the agent reported an error: {status.error}'
        else:
            reason = f'exited {status.exit}'
        record_failure(job, run, FailureKind.REMOTE, reason, status)
    message = status.final.strip()
    if len(message) > SUMMARY_LIMIT:
        message = message[:SUMMARY_LIMIT].rstrip() + '...'
    job.write_bytes(REPORT, f'{message}\n\nRun {run_id} on vanpi. Full report: pi_agent.py status {run_id}'.encode())
    job.remove(REPORT_CHECKS)
    return deliver(job, settings)


def blank_report(job, run, status):
    """A clean finish whose final message is blank: read again, then fail without relaunching."""
    run_id = run.get('run_id')
    checks = job.read(REPORT_CHECKS) or {}
    count = checks.get('count') if checks.get('run_id') == run_id and type(checks.get('count')) is int else 0
    count += 1
    if count >= REPORT_CHECK_LIMIT:
        job.remove(REPORT_CHECKS)
        record_failure(job, run, FailureKind.NO_REPORT,
                       f'finished without a report (blank on {count} checks); it is not relaunched '
                       'automatically, retry or run-now starts a new run', status)
    job.write(REPORT_CHECKS, {'run_id': run_id, 'count': count})
    raise NotReady(f'Run {run_id} finished but its report is blank (check {count} of {REPORT_CHECK_LIMIT}); '
                   f'the next check reads it again.\n{remote.launcher_commands(run_id=run_id)}')


def record_failure(job, record, kind, reason, status=None):
    """Ends the attempt: saves which run failed, for which attempt, and how."""
    run_id, token = record.get('run_id'), record.get('token')
    job.write(FAILURE, {'run_id': run_id, 'token': token, 'attempt': record.get('attempt'),
                        'kind': kind.value, 'reason': reason})
    if status is not None:
        print(status.text.rstrip())
    what = f'Run {run_id}' if run_id else f'The launch with token {token}'
    raise Failure(f'{what} on vanpi failed: {reason}.\n{remote.launcher_commands(run_id=run_id, token=token)}')


def deliver(job, settings):
    """Sends the saved report; a lost ntfy answer can send it twice (at-least-once)."""
    body = (JOB / REPORT).read_bytes()
    result = subprocess.run(['/bin/zsh', settings['agent_notify'], str(JOB)], input=body)
    if result.returncode != 0:
        raise Failure('The result could not be sent; the saved report is sent again at the next check.')
    job.write_bytes(DELIVERED, b'')
    return DONE


def main(argv):
    sys.stdout.reconfigure(line_buffering=True)  # interleaves with child output in last.log
    if argv in ([], ['collect']):
        return run_pass(collect_only=bool(argv))
    print('usage: run [collect]', file=sys.stderr)
    return USAGE


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
