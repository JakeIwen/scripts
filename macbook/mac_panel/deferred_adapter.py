"""Deferred-job inventory and guarded commands, never direct job-file writes."""
from datetime import datetime
import re
import os
from pathlib import Path
import tempfile
import time

from background_tasks import BackgroundTasks
from cli_runner import checked, json_result
from log_redaction import redact
from panel_contracts import AgentHost, ApiError, DeferredPayload, JobState, RemoteState, Reply, Section
from panel_paths import DATA, DEFERRED, HOME
from wire_validation import confirm, field, object_fields

# A job's name (deferred.zsh valid_name), and its id: the name, or the dated YYYY-MM-DD-NAME.
NAME_LIMIT = 60
NAME = re.compile(r'^[a-z0-9][a-z0-9-]{0,%d}$' % (NAME_LIMIT - 1))
JOB_ID = re.compile(r'^(\d{4}-\d{2}-\d{2}-)?[a-z0-9][a-z0-9-]{0,%d}$' % (NAME_LIMIT - 1))
DUE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')
# What `--relaunch` names: a Pi launch's token or run id (deferred_pi_state.TOKEN and RUN_ID).
TOKEN = re.compile(r'^d[0-9a-f]{16}$')
RUN_ID = re.compile(r'^\d{4}-\d{6}-[0-9a-f]{4}(?:-[a-z0-9][a-z0-9-]*)?$')
ACTIONS = frozenset(('cancel', 'retry', 'run-now', 'reschedule'))
MAC_MODEL = re.compile(r'^[a-z0-9][a-z0-9.\[\]-]{0,63}$')
PI_TIERS = ('astra', 'sol', 'luna')  # pi_agent.py --agent; a vanpi job's model
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
PATH_LIMIT, READY_LIMIT = 1024, 500
# deferred.zsh cancel: a vanpi run may still be active, so the job was kept (`--abandon` overrides).
CANCEL_REFUSED = 6
# retry/run-now: the job's Pi attempt may still be active and the request did not name it.
RELAUNCH_REFUSED = 7
# reschedule: a vanpi job that has launched is moved on only by retry or run-now.
RESCHEDULE_REFUSED = 8
CANCEL_TIMEOUT = 200  # the CLI bounds a vanpi cancel's Pi calls to 180 s
# deferred.zsh refuses a shorter vanpi --timeout (deferred_pi_run.MIN_PASS_TIMEOUT), and asks
# PI_READY_ALLOWANCE more of one with a ready command (deferred_pi_run.READY_ALLOWANCE).
PI_MIN_TIMEOUT, PI_READY_ALLOWANCE = 390, 30
AGENT_FIELDS = ('host', 'prompt', 'pi_prompt_path', 'model', 'effort', 'cwd', 'ready', 'run_timeout')


def validate_job_id(value):
    if not isinstance(value, str) or not JOB_ID.fullmatch(value):
        raise ApiError(400, 'Invalid job id')
    return value


def validate_due(value):
    """A local time that exists: a date like 02-30, or a clock time skipped by daylight saving, does not."""
    if not isinstance(value, str) or not DUE.fullmatch(value):
        raise ApiError(400, 'Due time must be YYYY-MM-DD HH:MM')
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d %H:%M')
    except ValueError:
        raise ApiError(400, 'Due time is not a valid local date and time') from None
    stamp = time.mktime((*parsed.timetuple()[:8], -1))
    if time.strftime('%Y-%m-%d %H:%M', time.localtime(stamp)) != value:
        raise ApiError(400, 'Due time does not exist in local time (a daylight-saving gap)')
    return value


def encodable(value):
    """Text the CLI and the staged file can carry: UTF-8 encodable, no NUL."""
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        return False
    return '\0' not in value


def decode_jobs(value):
    """The CLI's inventory. A malformed stream fails closed (502); a job whose agent or
    Pi fields contradict each other becomes an invalid row, and the other rows stay."""
    object_fields(value, ('checker', 'jobs_dir', 'jobs'), status=502)
    checker = field(value, 'checker', dict)
    for key in ('agent_loaded', 'stale'):
        field(checker, key, bool)
    field(checker, 'last_run', str, nullable=True)
    field(value, 'jobs_dir', str)
    jobs = field(value, 'jobs', list)
    for index, job in enumerate(jobs):
        object_fields(job, ('id', 'valid'), status=502)
        field(job, 'id', str)
        if not field(job, 'valid', bool):
            field(job, 'problem', str, nullable=True)
            continue
        for key in ('name', 'due', 'state', 'description', 'created', 'origin'):
            field(job, key, str)
        for key in ('attempts', 'max_attempts', 'timeout'):
            if field(job, key, int) < 0:
                raise ApiError(502, f'Invalid job {key}')
        field(job, 'last_log', str, nullable=True)
        field(job, 'give_up', str)
        field(job, 'agent', bool)
        field(job, 'ready', str, nullable=True)
        field(job, 'last_not_ready', str, nullable=True)
        if field(job, 'not_ready_checks', int) < 0:
            raise ApiError(502, 'Invalid job not_ready_checks')
        if job['state'] not in tuple(JobState):
            raise ApiError(502, 'Invalid job state')
        object_fields(job, AGENT_RECORD, status=502)
        problem = agent_fields_problem(job)
        if problem:
            jobs[index] = {'id': job['id'], 'valid': False, 'problem': problem}
    return value


AGENT_RECORD = ('host', 'model', 'effort', 'remote_run', 'remote_state', 'remote_token', 'relaunch_id')
# The states whose attempt may still be active with nothing left to collect: only they name a relaunch_id.
RELAUNCH_STATES = (RemoteState.LAUNCH_UNKNOWN, RemoteState.FAILED)
# Which Pi run fields each remote state carries: (run id, token), True required, False absent, None either.
REMOTE_FIELDS = {
    RemoteState.UNLAUNCHED: (False, False), RemoteState.LAUNCHING: (False, True),
    RemoteState.LAUNCH_UNKNOWN: (False, True), RemoteState.RUNNING: (True, True),
    RemoteState.FINISHED: (None, None), RemoteState.FAILED: (None, None),
}


def agent_fields_problem(job):
    """Why a job's agent and Pi fields do not fit together, or None when they do."""
    if not all(job[key] is None or isinstance(job[key], str) for key in AGENT_RECORD):
        return 'agent fields of the wrong type'
    if not job['agent']:
        return None if all(job[key] is None for key in AGENT_RECORD) else 'a script job with agent fields'
    if job['host'] not in tuple(AgentHost) or not job['model'] or not job['effort']:
        return 'an agent job without a known host, model and effort'
    if job['host'] == AgentHost.MAC:
        return None if all(job[key] is None for key in AGENT_RECORD[3:]) else 'a Mac agent job with Pi run fields'
    if job['remote_state'] not in tuple(RemoteState):
        return 'an unknown Pi run state'
    run, token = REMOTE_FIELDS[RemoteState(job['remote_state'])]
    if job['remote_state'] in (RemoteState.FINISHED, RemoteState.FAILED) and not (job['remote_run'] or job['remote_token']):
        return f'a {job["remote_state"]} Pi run with no run id or token'
    for key, rule in (('remote_run', run), ('remote_token', token)):
        if rule is not None and bool(job[key]) != rule:
            return f'a {job["remote_state"]} Pi run {"without" if rule else "with"} {key}'
    if job['relaunch_id'] is not None and (job['remote_state'] not in RELAUNCH_STATES
                                           or job['relaunch_id'] not in (job['remote_run'], job['remote_token'])):
        return f'a {job["remote_state"]} Pi run with a relaunch id that is not its own'
    return None


class DeferredAdapter:
    def __init__(self, runner, tasks=None, data=DATA):
        self.data = data
        self.runner = runner
        self.tasks = tasks or BackgroundTasks()

    def read(self) -> DeferredPayload:
        value = decode_jobs(json_result(self.runner.run((*DEFERRED, 'list', '--json'))))
        return {**value, 'tasks': self.tasks.list(Section.DEFERRED)}

    def mutate(self, body):
        object_fields(body, ('action',), ('id', 'due', 'confirmed', 'name', 'description', 'abandon',
                                           'relaunch', 'script', 'attempts', 'timeout', *AGENT_FIELDS))
        if body['action'] == 'add':
            return self.add(body)
        if body['action'] == 'add-agent':
            return self.add_agent(body)
        object_fields(body, ('action', 'id'), ('due', 'confirmed', 'abandon', 'relaunch'))
        action = field(body, 'action', str, status=400)
        if action not in ACTIONS:
            raise ApiError(400, 'Unsupported deferred action')
        job_id = validate_job_id(body['id'])
        if 'abandon' in body and (action != 'cancel' or field(body, 'abandon', bool, status=400) is not True):
            raise ApiError(400, 'abandon is only true, and only for cancel')
        relaunch = relaunch_token(body, action)
        argv = (*DEFERRED, action, *(('--abandon',) if 'abandon' in body else ()), job_id,
                *(('--relaunch', relaunch) if relaunch else ()))
        if action in ('cancel', 'run-now'):
            confirm(body)
        if action == 'reschedule':
            argv += (validate_due(body.get('due')),)
        if action == 'run-now':
            job = self.valid_job(job_id)
            if relaunch != job['relaunch_id']:
                raise relaunch_refused(job['relaunch_id'], relaunch)
            timeout = job['timeout'] + 30
            task = self.tasks.start(Section.DEFERRED, job_id, lambda: self.runner.run(argv, timeout=timeout))
            return Reply(task, 202)
        result = self.runner.run(argv, **({'timeout': CANCEL_TIMEOUT} if action == 'cancel' else {}))
        if result.returncode == 75:
            raise ApiError(409, 'The checker is running jobs; try again shortly')
        if action == 'cancel' and result.returncode == CANCEL_REFUSED:
            raise ApiError(409, redact((result.stderr or result.stdout).strip()), code='remote-active')
        if action == 'retry' and result.returncode == RELAUNCH_REFUSED:
            raise ApiError(409, redact((result.stderr or result.stdout).strip()), code='relaunch-unconfirmed')
        if action == 'reschedule' and result.returncode == RESCHEDULE_REFUSED:
            raise ApiError(409, redact((result.stderr or result.stdout).strip()), code='reschedule-launched')
        checked(result)
        return Reply({'message': result.stdout.strip() or 'Updated'})

    def valid_job(self, job_id):
        job = next((j for j in self.read()['jobs'] if j['id'] == job_id and j['valid']), None)
        if job is None:
            raise ApiError(404, 'Valid job not found')
        return job

    def add(self, body):
        object_fields(body, ('action', 'name', 'due', 'description', 'script'),
                      ('attempts', 'timeout', 'confirmed'))
        confirm(body)
        name = field(body, 'name', str, status=400)
        if not NAME.fullmatch(name):
            raise ApiError(400, f'Invalid job name: lower-case letters, digits and dashes, at most {NAME_LIMIT}')
        due = validate_due(body['due'])
        script = field(body, 'script', str, status=400)
        description = field(body, 'description', str, status=400)
        if not script.startswith('#!') or not encodable(script) or not encodable(description):
            raise ApiError(400, 'Script must start with #! and text must be UTF-8 without NUL')
        argv = (*DEFERRED, 'add')
        for key in ('attempts', 'timeout'):
            if key in body:
                value = field(body, key, int, status=400)
                if value < 1:
                    raise ApiError(400, f'{key} must be positive')
                argv += (f'--{key}', str(value))
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data.chmod(0o700)
        descriptor, temporary = tempfile.mkstemp(prefix='deferred-', suffix='.zsh', dir=self.data)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                os.fchmod(output.fileno(), 0o700)
                output.write(script)
            result = checked(self.runner.run((*argv, due, name, temporary, description)))
            return Reply({'message': result.stdout.strip() or 'Added'})
        finally:
            Path(temporary).unlink(missing_ok=True)

    def add_agent(self, body):
        """Validates everything first; only then stages a pasted prompt and runs add-agent."""
        object_fields(body, ('action', 'name', 'due', 'description', 'host', 'confirmed'),
                      ('attempts', 'timeout', *AGENT_FIELDS))
        confirm(body)
        options, prompt, pi_path = agent_options(body)
        name = field(body, 'name', str, status=400)
        if not NAME.fullmatch(name):
            raise ApiError(400, f'Invalid job name: lower-case letters, digits and dashes, at most {NAME_LIMIT}')
        due = validate_due(body['due'])
        description = field(body, 'description', str, status=400)
        if not encodable(description):
            raise ApiError(400, 'Description must be UTF-8 text without NUL')
        argv = (*DEFERRED, 'add-agent', *options)
        if pi_path is not None:
            result = checked(self.runner.run((*argv, due, name, f'vanpi:{pi_path}', description)))
            return Reply({'message': result.stdout.strip() or 'Added'})
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data.chmod(0o700)
        descriptor, temporary = tempfile.mkstemp(prefix='deferred-agent-', suffix='.md', dir=self.data)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(prompt)
            result = checked(self.runner.run((*argv, due, name, temporary, description)))
            return Reply({'message': result.stdout.strip() or 'Added'})
        finally:
            Path(temporary).unlink(missing_ok=True)


def relaunch_token(body, action):
    """The unresolved Pi attempt (token or run id) the user confirmed starting another run over, or None.

    The CLI checks it under the checker lock against the attempt it finds; a
    missing, different or stale id is refused there (exit 7), whatever the page saw."""
    if 'relaunch' not in body:
        return None
    value = field(body, 'relaunch', str, status=400)
    if action not in ('retry', 'run-now') or not (TOKEN.fullmatch(value) or RUN_ID.fullmatch(value)):
        raise ApiError(400, 'relaunch is a Pi launch token or run id, only for retry and run-now')
    confirm(body)
    return value


def relaunch_refused(expected, given):
    if expected is None:
        message = f'Nothing on vanpi is unresolved for this job, so relaunch {given} no longer applies; reload'
    else:
        message = (f'The Pi attempt {expected} may still be active; confirm starting another run over it')
    return ApiError(409, message, code='relaunch-unconfirmed')


def one_line(value, limit):
    return bool(value) and encodable(value) and '\n' not in value and '\r' not in value and len(value) <= limit


def agent_prompt(body, host):
    """Exactly one of pasted text or (vanpi only) a path on the Pi; returns (text, pi path)."""
    if ('prompt' in body) == ('pi_prompt_path' in body):
        raise ApiError(400, 'Give the prompt text or a Pi prompt path, not both')
    if 'prompt' in body:
        prompt = field(body, 'prompt', str, status=400)
        if not prompt.strip() or not encodable(prompt):
            raise ApiError(400, 'Prompt must be non-blank UTF-8 text without NUL')
        return prompt, None
    if host != AgentHost.VANPI:
        raise ApiError(400, 'A Pi prompt path needs host vanpi')
    path = field(body, 'pi_prompt_path', str, status=400)
    if not path.startswith('/') or not one_line(path, PATH_LIMIT):
        raise ApiError(400, f'Pi prompt path must be one absolute path of at most {PATH_LIMIT} characters')
    return None, path


def mac_directory(cwd):
    """An existing directory under the home folder, named by its canonical absolute path."""
    home = os.path.realpath(HOME)
    try:
        canonical = os.path.isabs(cwd) and os.path.realpath(cwd) == cwd and os.path.isdir(cwd)
    except ValueError:
        canonical = False
    return canonical and one_line(cwd, PATH_LIMIT) and (cwd == home or cwd.startswith(home + '/'))


def agent_options(body):
    """The add-agent flags for a request body, plus (prompt text, Pi prompt path)."""
    host = field(body, 'host', str, status=400)
    if host not in tuple(AgentHost):
        raise ApiError(400, 'host must be mac or vanpi')
    prompt, pi_path = agent_prompt(body, host)
    options = ['--host', host]
    if 'model' in body:
        model = field(body, 'model', str, status=400)
        if not (model in PI_TIERS if host == AgentHost.VANPI else MAC_MODEL.fullmatch(model)):
            raise ApiError(400, 'model must be astra, sol or luna on vanpi, or a Claude model name on the Mac')
        options += ['--model', model]
    if 'effort' in body:
        if field(body, 'effort', str, status=400) not in EFFORTS:
            raise ApiError(400, 'effort must be low, medium, high, xhigh or max')
        options += ['--effort', body['effort']]
    cwd = field(body, 'cwd', str, status=400) if 'cwd' in body else None
    if host == AgentHost.MAC:
        cwd = cwd or str(HOME)  # never the panel's own working directory
        if not mac_directory(cwd):
            raise ApiError(400, 'cwd must be the canonical path of an existing folder under your home folder')
    elif cwd is not None and not (cwd.startswith('/') and one_line(cwd, PATH_LIMIT)):
        raise ApiError(400, f'cwd on vanpi must be one absolute path of at most {PATH_LIMIT} characters')
    elif cwd is not None and under_mac_home(cwd):
        # pi_agent.py rewrites a path under this Mac's home to the Pi's home, so it is ambiguous.
        raise ApiError(400, "cwd on vanpi must not be under this Mac's home folder; give a real path on the Pi")
    if cwd is not None:
        options += ['--cwd', cwd]
    if 'ready' in body:
        if not one_line(field(body, 'ready', str, status=400), READY_LIMIT):
            raise ApiError(400, f'ready must be one line of at most {READY_LIMIT} characters')
        options += ['--ready', body['ready']]
    if 'run_timeout' in body and host != AgentHost.VANPI:
        raise ApiError(400, 'run_timeout applies only to vanpi')
    for key in ('attempts', 'timeout', 'run_timeout'):
        if key in body:
            if field(body, key, int, status=400) < 1:
                raise ApiError(400, f'{key} must be positive')
            options += [f'--{key.replace("_", "-")}', str(body[key])]
    if host == AgentHost.VANPI:
        vanpi_limits(body)
    return tuple(options), prompt, pi_path


def vanpi_limits(body):
    """A vanpi job starts one Pi run by itself, and each pass must have room to launch it."""
    if body.get('attempts', 1) != 1:
        raise ApiError(400, 'attempts does not apply to vanpi: such a job starts at most one Pi run by itself; '
                            'Retry or Run now starts another')
    floor = PI_MIN_TIMEOUT + (PI_READY_ALLOWANCE if 'ready' in body else 0)
    if body.get('timeout', floor) < floor:
        raise ApiError(400, f'timeout on vanpi must be at least {floor} seconds'
                            f'{" with a ready condition" if "ready" in body else ""}')


def under_mac_home(path):
    homes = {str(HOME), os.path.realpath(HOME)}
    return any(path == home or path.startswith(home + '/') for home in homes)
