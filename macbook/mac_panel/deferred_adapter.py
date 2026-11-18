"""Deferred-job inventory and guarded commands, never direct job-file writes."""
from datetime import datetime
import re
import os
from pathlib import Path
import tempfile

from background_tasks import BackgroundTasks
from cli_runner import checked, json_result
from log_redaction import redact
from panel_contracts import AgentHost, ApiError, DeferredPayload, JobState, RemoteState, Reply, Section
from panel_paths import DATA, DEFERRED, HOME
from wire_validation import confirm, field, object_fields

JOB_ID = re.compile(r'^(\d{4}-\d{2}-\d{2}-)?[a-z0-9][a-z0-9-]{0,59}$')
DUE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')
NAME = re.compile(r'^[a-z0-9][a-z0-9-]{0,59}$')
ACTIONS = frozenset(('cancel', 'retry', 'run-now', 'reschedule'))
MAC_MODEL = re.compile(r'^[a-z0-9][a-z0-9.\[\]-]{0,63}$')
PI_TIERS = ('astra', 'sol', 'luna')  # pi_agent.py --agent; a vanpi job's model
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
PATH_LIMIT, READY_LIMIT = 1024, 500
# deferred.zsh cancel: a vanpi run may still be active, so the job was kept (`--abandon` overrides).
CANCEL_REFUSED = 6
CANCEL_TIMEOUT = 200  # the CLI bounds each of its Pi calls; a vanpi cancel makes up to four
AGENT_FIELDS = ('host', 'prompt', 'pi_prompt_path', 'model', 'effort', 'cwd', 'ready', 'run_timeout')


def validate_job_id(value):
    if not isinstance(value, str) or not JOB_ID.fullmatch(value):
        raise ApiError(400, 'Invalid job id')
    return value


def validate_due(value):
    if not isinstance(value, str) or not DUE.fullmatch(value):
        raise ApiError(400, 'Due time must be YYYY-MM-DD HH:MM')
    try:
        datetime.strptime(value, '%Y-%m-%d %H:%M')
    except ValueError:
        raise ApiError(400, 'Due time is not a valid local date and time') from None
    return value


def decode_jobs(value):
    object_fields(value, ('checker', 'jobs_dir', 'jobs'), status=502)
    checker = field(value, 'checker', dict)
    for key in ('agent_loaded', 'stale'):
        field(checker, key, bool)
    field(checker, 'last_run', str, nullable=True)
    field(value, 'jobs_dir', str)
    for job in field(value, 'jobs', list):
        object_fields(job, ('id', 'valid'), status=502)
        field(job, 'id', str)
        if not field(job, 'valid', bool):
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
        decode_agent_fields(job)
    return value


def decode_agent_fields(job):
    """Agent jobs name their host, model and effort; vanpi ones also their Pi run. Script jobs: all null."""
    object_fields(job, ('host', 'model', 'effort', 'remote_run', 'remote_state'), status=502)
    host = field(job, 'host', str, nullable=True)
    for key in ('model', 'effort', 'remote_run', 'remote_state'):
        field(job, key, str, nullable=True)
    if (host is None) == job['agent'] or (host is not None and host not in tuple(AgentHost)):
        raise ApiError(502, 'Invalid job host')
    if (job['remote_state'] is None) != (host != AgentHost.VANPI):
        raise ApiError(502, 'Invalid job remote_state')
    if job['remote_state'] is not None and job['remote_state'] not in tuple(RemoteState):
        raise ApiError(502, 'Invalid job remote_state')


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
                                           'script', 'attempts', 'timeout', *AGENT_FIELDS))
        if body['action'] == 'add':
            return self.add(body)
        if body['action'] == 'add-agent':
            return self.add_agent(body)
        object_fields(body, ('action', 'id'), ('due', 'confirmed', 'abandon'))
        action = field(body, 'action', str, status=400)
        if action not in ACTIONS:
            raise ApiError(400, 'Unsupported deferred action')
        job_id = validate_job_id(body['id'])
        if 'abandon' in body and (action != 'cancel' or field(body, 'abandon', bool, status=400) is not True):
            raise ApiError(400, 'abandon is only true, and only for cancel')
        argv = (*DEFERRED, action, *(('--abandon',) if 'abandon' in body else ()), job_id)
        if action in ('cancel', 'run-now'):
            confirm(body)
        if action == 'reschedule':
            argv += (validate_due(body.get('due')),)
        if action == 'retry' and body.get('confirmed') is not True and self.launch_unknown(job_id):
            # Retrying a launch whose outcome is unknown can start a second Pi run.
            confirm(body)
        if action == 'run-now':
            job = self.valid_job(job_id)
            timeout = job['timeout'] + 30
            task = self.tasks.start(Section.DEFERRED, job_id, lambda: self.runner.run(argv, timeout=timeout))
            return Reply(task, 202)
        result = self.runner.run(argv, **({'timeout': CANCEL_TIMEOUT} if action == 'cancel' else {}))
        if result.returncode == 75:
            raise ApiError(409, 'The checker is running jobs; try again shortly')
        if action == 'cancel' and result.returncode == CANCEL_REFUSED:
            raise ApiError(409, redact((result.stderr or result.stdout).strip()), code='remote-active')
        checked(result)
        return Reply({'message': result.stdout.strip() or 'Updated'})

    def valid_job(self, job_id):
        job = next((j for j in self.read()['jobs'] if j['id'] == job_id and j['valid']), None)
        if job is None:
            raise ApiError(404, 'Valid job not found')
        return job

    def launch_unknown(self, job_id):
        return self.valid_job(job_id).get('remote_state') == RemoteState.LAUNCH_UNKNOWN

    def add(self, body):
        object_fields(body, ('action', 'name', 'due', 'description', 'script'),
                      ('attempts', 'timeout', 'confirmed'))
        confirm(body)
        name = field(body, 'name', str, status=400)
        if not NAME.fullmatch(name):
            raise ApiError(400, 'Invalid job name')
        due = validate_due(body['due'])
        script = field(body, 'script', str, status=400)
        description = field(body, 'description', str, status=400)
        if not script.startswith('#!') or '\0' in script or '\0' in description:
            raise ApiError(400, 'Script must start with #! and text cannot contain NUL')
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
            raise ApiError(400, 'Invalid job name')
        due = validate_due(body['due'])
        description = field(body, 'description', str, status=400)
        if '\0' in description:
            raise ApiError(400, 'Description cannot contain NUL')
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


def one_line(value, limit):
    return bool(value) and '\0' not in value and '\n' not in value and '\r' not in value and len(value) <= limit


def agent_prompt(body, host):
    """Exactly one of pasted text or (vanpi only) a path on the Pi; returns (text, pi path)."""
    if ('prompt' in body) == ('pi_prompt_path' in body):
        raise ApiError(400, 'Give the prompt text or a Pi prompt path, not both')
    if 'prompt' in body:
        prompt = field(body, 'prompt', str, status=400)
        if not prompt.strip() or '\0' in prompt:
            raise ApiError(400, 'Prompt must be non-blank text without NUL')
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
    return tuple(options), prompt, pi_path
