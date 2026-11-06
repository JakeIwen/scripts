"""Deferred-job inventory and guarded commands, never direct job-file writes."""
from datetime import datetime
import re
import os
from pathlib import Path
import tempfile

from background_tasks import BackgroundTasks
from cli_runner import checked, json_result
from panel_contracts import ApiError, DeferredPayload, JobState, Reply
from panel_paths import DEFERRED, DATA
from wire_validation import confirm, field, object_fields

JOB_ID = re.compile(r'^(\d{4}-\d{2}-\d{2}-)?[a-z0-9][a-z0-9-]{0,59}$')
DUE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')
NAME = re.compile(r'^[a-z0-9][a-z0-9-]{0,59}$')
ACTIONS = frozenset(('cancel', 'retry', 'run-now', 'reschedule'))


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
        if job['state'] not in tuple(JobState):
            raise ApiError(502, 'Invalid job state')
    return value


class DeferredAdapter:
    def __init__(self, runner, tasks=None, data=DATA):
        self.data = data
        self.runner = runner
        self.tasks = tasks or BackgroundTasks()

    def read(self) -> DeferredPayload:
        value = decode_jobs(json_result(self.runner.run((*DEFERRED, 'list', '--json'))))
        return {**value, 'tasks': self.tasks.list()}

    def mutate(self, body):
        object_fields(body, ('action',), ('id', 'due', 'confirmed', 'name', 'description',
                                           'script', 'attempts', 'timeout'))
        if body['action'] == 'add':
            return self.add(body)
        object_fields(body, ('action', 'id'), ('due', 'confirmed'))
        action = field(body, 'action', str, status=400)
        if action not in ACTIONS:
            raise ApiError(400, 'Unsupported deferred action')
        job_id = validate_job_id(body['id'])
        argv = (*DEFERRED, action, job_id)
        if action in ('cancel', 'run-now'):
            confirm(body)
        if action == 'reschedule':
            argv += (validate_due(body.get('due')),)
        if action == 'run-now':
            jobs = self.read()['jobs']
            job = next((j for j in jobs if j['id'] == job_id and j['valid']), None)
            if job is None:
                raise ApiError(404, 'Valid job not found')
            timeout = job['timeout'] + 30
            task = self.tasks.start(job_id, lambda: self.runner.run(argv, timeout=timeout))
            return Reply(task, 202)
        result = self.runner.run(argv)
        if result.returncode == 75:
            raise ApiError(409, 'The checker is running jobs; try again shortly')
        checked(result)
        return Reply({'message': result.stdout.strip() or 'Updated'})

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
