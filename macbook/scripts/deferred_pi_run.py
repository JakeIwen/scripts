#!/usr/bin/python3 -IB
"""Run script of a job queued by `deferred.zsh add-agent --host vanpi`.

Copied into the job directory as `run`, beside the job's agent.env, so the job
stands alone. Each pass (no arguments; deferred.zsh runs it with the job
directory as the working directory) runs the saved prompt on vanpi through
pi_agent.py and sends the agent's final message to the phone. A Pi run outlives
the pass that started it, so every pass first dispatches on the durable state
files below; only a job with no launch on record evaluates --ready or reads the
prompt. Exit 75 means "not finished yet" to deferred.zsh: no attempt spent.

deferred.zsh also calls it with a verb: `describe` (remote state and run id for
`list --json`), `cancel [--abandon]` (stop a possibly active Pi run before the
job is deleted) and `clear-hold` (`retry`/`run-now` saying "launch it again").
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

JOB = Path(__file__).resolve().parent
PI_HOST = 'vanpi'
LAUNCHER = Path.home() / '.claude/skills/pi-agent/pi_agent.py'
# The same options pi_agent.py uses, so both reach the Pi the same way.
SSH_OPTIONS = ['-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
               '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=4']
SSH_UNREACHABLE = 255

DONE, FAILED, USAGE, NOT_READY = 0, 1, 2, 75
REFUSED = 6  # cancel: a Pi run may still be active; deferred.zsh relays it
LAUNCHER_USAGE = 2  # pi_agent.py: usage or launch error, nothing was started

PROMPT_LIMIT = 256 * 1024
SUMMARY_LIMIT = 600
LIST_LIMIT = 500  # runs listed to find a launch token; the Pi prunes runs after 30 days
# Hard local timeouts per call; each sits above pi_agent.py's own ssh timeout
# (60 s for status, list and stop, 120 s to start a run) so the launcher
# normally gives up first and reports why.
SSH_TIMEOUT, STATUS_TIMEOUT, LAUNCH_TIMEOUT = 30, 90, 180
CANCEL_CALL_TIMEOUT = 45  # cancel runs while the panel waits on it
# After a stop the run needs a few seconds to end (SIGTERM, then the launcher
# records the exit), so its status is polled for this long before the stop
# counts as unconfirmed. Worst case for cancel, which the panel waits 200 s on:
# 3 calls of 45 s, this window, and one last read of at most STOP_MIN_READ.
STOP_CONFIRM_WINDOW, STOP_CONFIRM_INTERVAL, STOP_MIN_READ = 30, 3, 10
POLL_WINDOW, POLL_INTERVAL = 240, 20
# Seconds of the job's --timeout kept for launching, stopping and the final
# status read; only the rest is spent polling a running run.
POLL_RESERVE, PASS_MARGIN = 600, 60

RUN_ID = re.compile(r'^\d{4}-\d{6}-[0-9a-f]{4}(?:-[a-z0-9][a-z0-9-]*)?$')
STARTED = re.compile(r'^started (\S+) \(', re.M)
RUN_LINE = re.compile(r'^run:\s+(\S+) on \S')
STATE_LINE = re.compile(r'^state:\s+(done|running|died)(?:, exit (-?\d+))?(?:,|$)')
FINAL_DELIMITER = '--- final message'
NO_MESSAGE = '(none)'  # what the Pi prints for a run that left no result.md

# Durable state, one file each, written atomically; their presence is the state.
DELIVERED = 'delivered'          # the result reached the phone
REPORT = 'report.txt'            # validated notification body awaiting delivery
RUN = 'remote-run.json'          # {run_id, token, launched}: a run exists on the Pi
LAUNCHING = 'launching.json'     # {token, at}: a launch was attempted, outcome not yet known
HOLD = 'launch-unknown.json'     # {token, at, reason}: the launch could not be reconciled
FAILURE = 'remote-failed.json'   # {run_id, reason}: the current attempt's run failed
HISTORY = 'runs.log'             # earlier attempts' run ids and abandoned tokens
FETCHED = 'prompt.fetched'       # the vanpi: prompt as read at launch
INLINE_PROMPT = 'prompt.txt'

SETTINGS = ('agent_name', 'agent_model', 'agent_effort', 'agent_cwd', 'agent_ready',
            'agent_pi_prompt', 'agent_run_timeout', 'agent_notify')


class RemoteState(str, Enum):
    UNLAUNCHED = 'unlaunched'
    LAUNCHING = 'launching'
    LAUNCH_UNKNOWN = 'launch-unknown'
    RUNNING = 'running'
    FINISHED = 'finished'
    FAILED = 'failed'


class NotReady(Exception):
    """Nothing is wrong with the job yet; the next pass tries again (exit 75)."""


class Failure(Exception):
    """An ordinary failure of this attempt (exit 1)."""


class Refused(Exception):
    """cancel cannot show that no Pi run is left active."""


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


# ---- small durable-state helpers ----

def read_json(name):
    """The saved record, or None when absent; a damaged record stops the pass."""
    try:
        text = (JOB / name).read_text()
    except FileNotFoundError:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        raise Failure(f'{name} in {JOB} is unreadable; inspect it before retrying') from None
    if not isinstance(value, dict):
        raise Failure(f'{name} in {JOB} is unreadable; inspect it before retrying')
    return value


def write_atomic(name, data):
    temporary = JOB / f'.{name}.tmp'
    with open(temporary, 'wb') as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, JOB / name)


def write_json(name, value):
    write_atomic(name, (json.dumps(value) + '\n').encode())


def remove(*names):
    for name in names:
        (JOB / name).unlink(missing_ok=True)


def append_history(line):
    with open(JOB / HISTORY, 'a', encoding='utf-8') as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(f'{time.strftime("%Y-%m-%dT%H:%M")} {line}\n')


def job_timeout():
    """The job's --timeout: the watchdog on this pass."""
    try:
        for line in (JOB / 'job').read_text().splitlines():
            if line.startswith('timeout=') and line[8:].isdigit():
                return int(line[8:])
    except OSError:
        pass
    return 900


def load_settings():
    """agent.env is zsh-quoted; zsh decodes it and hands each value back NUL-terminated."""
    script = 'source "$1/agent.env" || exit 1; shift; for k; do print -rn -- "${(P)k-}"; print -rn -- $\'\\0\'; done'
    result = subprocess.run(['/bin/zsh', '-f', '-c', script, 'settings', str(JOB), *SETTINGS],
                            capture_output=True, timeout=30)
    if result.returncode != 0:
        raise Failure(f'cannot read {JOB}/agent.env')
    values = result.stdout.decode('utf-8', 'surrogateescape').split('\0')[:-1]
    return dict(zip(SETTINGS, values))


# ---- commands on this Mac and the Pi ----

def bounded(argv, timeout, data=None):
    """Runs argv under a hard timeout; None when it had to be killed."""
    try:
        return subprocess.run(argv, input=data, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def launcher(args, timeout):
    return bounded(['/usr/bin/python3', '-I', '-B', str(LAUNCHER), *args], timeout)


def ssh(command, timeout, data=b''):
    return bounded(['ssh', *SSH_OPTIONS, PI_HOST, command], timeout, data)


def text(result):
    return (result.stdout + result.stderr).decode('utf-8', 'replace').strip() if result else ''


def launcher_commands(run_id=None, token=None):
    base = f'/usr/bin/python3 {LAUNCHER}'
    if run_id:
        return f'Check it: {base} status {run_id}\nStop it: {base} stop {run_id}'
    return (f'Look for it: {base} status -n {LIST_LIMIT} | grep -e -{token}\n'
            f'Stop what it finds: {base} stop <run id>')


@dataclass(frozen=True)
class RunStatus:
    state: str          # done, running or died
    exit: str | None
    error: str | None
    final: str | None   # the text after the final-message delimiter; None while running
    text: str

    @property
    def succeeded(self):
        return (self.state == 'done' and self.exit == '0' and not self.error
                and bool(self.final) and self.final.strip() not in ('', NO_MESSAGE))


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


def run_status(run_id, timeout=STATUS_TIMEOUT):
    """A parsed status for run_id; None when the Pi or the report could not be read."""
    result = launcher(['status', run_id], timeout)
    if result is None or result.returncode != 0:
        return None
    return parse_status(result.stdout.decode('utf-8', 'replace'), run_id)


def runs_with_token(token, timeout=STATUS_TIMEOUT):
    """Run ids on the Pi ending in -TOKEN; None when the Pi could not be listed."""
    result = launcher(['status', '-n', str(LIST_LIMIT)], timeout)
    if result is None or result.returncode != 0:
        return None
    ids = [line.split()[0] for line in result.stdout.decode('utf-8', 'replace').splitlines() if line.split()]
    return [run_id for run_id in ids if RUN_ID.match(run_id) and run_id.endswith('-' + token)]


def stop_confirmed(run_id, timeout=STATUS_TIMEOUT):
    """Sends the stop, then polls for a parsed status that no longer says running.

    Returns that status, or None while the stop is unconfirmed after
    STOP_CONFIRM_WINDOW. Only a positive terminal state confirms: an unreadable
    or malformed status is just another poll that did not confirm. It proves
    the launcher's recorded state, not that every detached descendant is gone.
    """
    launcher(['stop', run_id], timeout)
    deadline = time.monotonic() + STOP_CONFIRM_WINDOW
    while True:
        remaining = deadline - time.monotonic()
        status = run_status(run_id, min(timeout, max(STOP_MIN_READ, remaining)))
        if status and status.state != 'running':
            return status
        if time.monotonic() + STOP_CONFIRM_INTERVAL >= deadline:
            return None
        time.sleep(STOP_CONFIRM_INTERVAL)


# ---- one pass ----

class PassClock:
    """The pass's own budget: polling a running run stops well inside --timeout."""

    def __init__(self, timeout):
        self.timeout = timeout
        self.started = time.monotonic()

    def may_poll(self, collecting_since):
        now = time.monotonic()
        window = min(POLL_WINDOW, max(0, self.timeout - POLL_RESERVE))
        return (now + POLL_INTERVAL <= collecting_since + window
                and now + POLL_INTERVAL + STATUS_TIMEOUT <= self.started + self.timeout - PASS_MARGIN)


def run_pass():
    os.environ['PATH'] = f'{Path.home()}/.local/bin:{os.environ.get("PATH", "/usr/bin:/bin")}'
    try:
        return dispatch(PassClock(job_timeout()))
    except NotReady as reason:
        print(reason)
        return NOT_READY
    except Failure as reason:
        print(reason)
        return FAILED


def dispatch(clock):
    if (JOB / DELIVERED).exists():
        return DONE
    settings = load_settings()
    if (JOB / REPORT).exists():
        return deliver(settings)
    hold = read_json(HOLD)
    if hold is not None:
        raise Failure(held(hold.get('token'), hold.get('reason')))
    failure = read_json(FAILURE)
    if failure is not None:
        # A new attempt starts a new run; the failed one is history and never looked at again.
        append_history(f'run {failure.get("run_id")} failed: {failure.get("reason")}')
        remove(FAILURE, RUN, FETCHED)
    run = read_json(RUN)
    if run is not None:
        remove(LAUNCHING)  # a crash between saving the id and clearing the marker
        return collect(settings, run, clock)
    launching = read_json(LAUNCHING)
    if launching is not None:
        return reconcile(settings, launching, clock)
    return launch(settings, clock)


def launch(settings, clock):
    ready = settings['agent_ready']
    if ready and subprocess.run(['/bin/zsh', '-c', ready]).returncode != 0:
        raise NotReady('Not ready: the --ready command exited nonzero.')
    prompt = acquire_prompt(settings)
    reachable = ssh('true', SSH_TIMEOUT)
    if reachable is None or reachable.returncode != 0:
        raise NotReady('vanpi did not answer; the launch waits for the next check.')
    token = 'd' + secrets.token_hex(8)  # survives pi_agent.py's 30-character name slug whole
    at = int(time.time())
    write_json(LAUNCHING, {'token': token, 'at': at})
    result = launcher(['run', '--agent', settings['agent_model'], '--effort', settings['agent_effort'],
                       '--cd', settings['agent_cwd'], '--name', token, '--no-wait',
                       '--prompt-file', str(prompt)], LAUNCH_TIMEOUT)
    if result is not None and result.returncode == LAUNCHER_USAGE:
        remove(LAUNCHING)
        raise Failure(f'pi_agent.py refused the launch:\n{text(result)}')
    match = STARTED.search(result.stdout.decode('utf-8', 'replace')) if result and result.returncode == 0 else None
    run_id = match.group(1) if match else None
    if not run_id or not RUN_ID.match(run_id) or not run_id.endswith('-' + token):
        raise NotReady(f'The launch outcome is unknown ({text(result) or "no answer"}); '
                       f'the next check looks for token {token} on vanpi before doing anything else.')
    save_run(run_id, token, at)
    print(f'Started {run_id} on vanpi.')
    return collect(settings, read_json(RUN), clock)


def acquire_prompt(settings):
    """A local file holding the prompt to send; vanpi: prompts are read from the Pi now."""
    path = settings['agent_pi_prompt']
    if not path:
        try:
            data = (JOB / INLINE_PROMPT).read_bytes()
        except OSError as error:
            raise Failure(f'Cannot read the saved prompt: {error.strerror}') from None
        return checked_prompt(data, JOB / INLINE_PROMPT)
    result = ssh(REMOTE_READER_COMMAND, SSH_TIMEOUT, path.encode('utf-8', 'surrogateescape'))
    if result is None or result.returncode == SSH_UNREACHABLE:
        raise NotReady('vanpi did not answer; the prompt is read at the next check.')
    if result.returncode != 0:
        raise Failure(f'The prompt file vanpi:{path} cannot be used: '
                      f'{result.stderr.decode("utf-8", "replace").strip() or "exit " + str(result.returncode)}')
    write_atomic(FETCHED, result.stdout)
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


def save_run(run_id, token, launched):
    write_json(RUN, {'run_id': run_id, 'token': token, 'launched': launched})
    remove(LAUNCHING)


def reconcile(settings, launching, clock):
    """A launch whose answer was lost: adopt its run by token, never launch again."""
    token = launching.get('token')
    matches = runs_with_token(token)
    if matches is None:
        raise NotReady(f'vanpi could not be listed; token {token} is looked for at the next check.\n'
                       f'{launcher_commands(token=token)}')
    if len(matches) == 1:
        save_run(matches[0], token, launching.get('at') or int(time.time()))
        print(f'Found the run of token {token}: {matches[0]}.')
        return collect(settings, read_json(RUN), clock)
    reason = 'no run with that token' if not matches else f'{len(matches)} runs match'
    write_json(HOLD, {'token': token, 'at': launching.get('at'), 'reason': reason})
    remove(LAUNCHING)
    raise Failure(held(token, reason))


def held(token, reason):
    return (f'Held: the launch with token {token} is unknown ({reason}); it may be running on vanpi, '
            f'so no check launches it again.\n{launcher_commands(token=token)}\n'
            'Retry or run-now starts a new run; cancel --abandon drops the job.')


def collect(settings, run, clock):
    run_id = run.get('run_id')
    deadline = int(run.get('launched') or 0) + int(settings['agent_run_timeout'] or 120) * 60
    since = time.monotonic()
    status = run_status(run_id)
    while status is not None and status.state == 'running' and time.time() < deadline and clock.may_poll(since):
        time.sleep(POLL_INTERVAL)
        status = run_status(run_id)
    if status is None:
        raise NotReady(f'Could not read run {run_id} on vanpi; the next check tries again.\n'
                       f'{give_up_note(run_id)}')
    if status.state == 'running' and time.time() < deadline:
        raise NotReady(f'{give_up_note(run_id)}\n'
                       f'Pi run {run_id} is still running on vanpi; a later check collects it.')
    if status.state == 'running':
        status = stop_confirmed(run_id)
        if status is None:
            raise NotReady(f'Run {run_id} passed its --run-timeout; the stop is not confirmed yet '
                           f'and is tried again at the next check.\n{give_up_note(run_id)}')
        if not status.succeeded:
            return record_failure(run_id, 'timed out: stopped after its --run-timeout', status)
    return settle(settings, run_id, status)


def give_up_note(run_id):
    return ('If this job gives up first, the run is left on vanpi '
            f'(--run-timeout normally stops it first).\n{launcher_commands(run_id=run_id)}')


def settle(settings, run_id, status):
    if not status.succeeded:
        if status.state == 'died':
            reason = 'died without finishing (Pi reboot or kill)'
        elif status.error:
            reason = f'the agent reported an error: {status.error}'
        elif status.exit != '0':
            reason = f'exited {status.exit}'
        else:
            reason = 'finished without a final message'
        return record_failure(run_id, reason, status)
    message = status.final.strip()
    if len(message) > SUMMARY_LIMIT:
        message = message[:SUMMARY_LIMIT].rstrip() + '...'
    write_atomic(REPORT, f'{message}\n\nRun {run_id} on vanpi. Full report: pi_agent.py status {run_id}'.encode())
    return deliver(settings)


def record_failure(run_id, reason, status):
    write_json(FAILURE, {'run_id': run_id, 'reason': reason})
    print(status.text.rstrip())
    raise Failure(f'Run {run_id} on vanpi failed: {reason}.\n{launcher_commands(run_id=run_id)}')


def deliver(settings):
    """Sends the saved report; a lost ntfy answer can send it twice (at-least-once)."""
    body = (JOB / REPORT).read_bytes()
    result = subprocess.run(['/bin/zsh', settings['agent_notify'], str(JOB)], input=body)
    if result.returncode != 0:
        raise Failure('The result could not be sent; the saved report is sent again on retry.')
    write_atomic(DELIVERED, b'')
    return DONE


# ---- the verbs deferred.zsh calls ----

def remote_state():
    if (JOB / DELIVERED).exists() or (JOB / REPORT).exists():
        return RemoteState.FINISHED
    if (JOB / HOLD).exists():
        return RemoteState.LAUNCH_UNKNOWN
    if (JOB / FAILURE).exists():
        return RemoteState.FAILED
    if (JOB / RUN).exists():
        return RemoteState.RUNNING
    if (JOB / LAUNCHING).exists():
        return RemoteState.LAUNCHING
    return RemoteState.UNLAUNCHED


def describe():
    """Two lines for `list --json`: the remote state, then the run id (empty when none)."""
    try:
        run = read_json(RUN) or read_json(FAILURE) or {}
    except Failure:
        run = {}
    run_id = run.get('run_id')
    print(remote_state().value)
    print(run_id if isinstance(run_id, str) and RUN_ID.match(run_id) else '')
    return DONE


def cancel(abandon):
    """Exit 0 when the job may be deleted; REFUSED while a Pi run may still be active."""
    state = remote_state()
    if state not in (RemoteState.RUNNING, RemoteState.LAUNCHING, RemoteState.LAUNCH_UNKNOWN):
        return DONE
    record = read_json(RUN) if state == RemoteState.RUNNING else read_json(LAUNCHING) or read_json(HOLD)
    run_id, token = record.get('run_id'), record.get('token')
    if abandon:
        what = f'run {run_id}' if run_id else f'the launch with token {token}'
        print(f'Abandoned {what}; it may still be active on vanpi.\n'
              f'{launcher_commands(run_id=run_id, token=token)}')
        return DONE
    try:
        if not run_id:
            matches = runs_with_token(token, CANCEL_CALL_TIMEOUT)
            if matches is None:
                raise Refused(f'vanpi could not be listed to look for token {token}.')
            if len(matches) != 1:
                raise Refused(f'{len(matches) or "No"} runs on vanpi match token {token}, so it is unknown '
                              'whether that launch is running.')
            run_id = matches[0]
        status = run_status(run_id, CANCEL_CALL_TIMEOUT)
        if status is None:
            raise Refused(f'Could not read run {run_id} on vanpi.')
        if status.state == 'running' and stop_confirmed(run_id, CANCEL_CALL_TIMEOUT) is None:
            raise Refused(f'Run {run_id} on vanpi was sent a stop but still shows running (or could not be read).')
    except Refused as reason:
        print(f'{reason} The job is kept so its run is not left on vanpi unseen.\n'
              f'{launcher_commands(run_id=run_id, token=token)}\n'
              'cancel --abandon JOB deletes the job anyway.', file=sys.stderr)
        return REFUSED
    print(f'Run {run_id} on vanpi is not running.')
    return DONE


def clear_hold():
    """retry/run-now: the user chose to launch again; the abandoned token goes into the history."""
    hold = read_json(HOLD)
    if hold is not None:
        append_history(f'launch token {hold.get("token")} abandoned by retry '
                       f'({hold.get("reason")}; that launch may still be active)')
        remove(HOLD)
        print(f'Cleared the unknown launch {hold.get("token")}; the next attempt starts a new run.')
    return DONE


def main(argv):
    sys.stdout.reconfigure(line_buffering=True)  # interleaves with child output in last.log
    try:
        if not argv:
            return run_pass()
        if argv == ['describe']:
            return describe()
        if argv == ['clear-hold']:
            return clear_hold()
        if argv in (['cancel'], ['cancel', '--abandon']):
            return cancel(abandon=len(argv) == 2)
    except Failure as reason:
        print(reason, file=sys.stderr)
        return REFUSED if argv[:1] == ['cancel'] else FAILED
    print('usage: run [describe | clear-hold | cancel [--abandon]]', file=sys.stderr)
    return USAGE


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
