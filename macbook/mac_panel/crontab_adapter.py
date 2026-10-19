"""Crontab display plus guarded actions delegated to crontab_sync.py."""
import json
from pathlib import Path
import re
import shlex

from cli_runner import checked, json_result
from cron_schedule import describe_schedule
from log_redaction import redact, safe_display_path
from panel_contracts import ApiError, CronJob, CrontabPayload, OwnerKind, Reply
from panel_paths import ROOT
from wire_validation import confirm, field, object_fields

REVISION = re.compile(r'^[0-9a-f]{64}$')
JOB_ID = re.compile(r'^[0-9a-f]{12}$')
ACTIONS = frozenset(('pause', 'resume', 'install'))
SYNC = ('/usr/bin/python3', '-B', str(ROOT / 'macbook/scripts/crontab_sync.py'))
# Tokens shlex emits for the shell punctuation the summary drops.
COMMAND_SEPARATORS = frozenset(('&&', '||', ';'))
REDIRECTIONS = frozenset(('>', '>>', '<', '>&', '&>', '<<'))
INTERPRETERS = frozenset(('python3', 'python', 'zsh', 'bash', 'sh', 'env'))
ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')


def command_words(command):
    """Words of the command that does the work: the last `&&`/`;` step, before any pipe."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars='><&|;')
    lexer.whitespace_split = True
    steps, current = [], []
    for token in lexer:
        if token in COMMAND_SEPARATORS:
            steps.append(current)
            current = []
        else:
            current.append(token)
    steps.append(current)
    words = next((step for step in reversed(steps) if step), [])
    return words[:words.index('|')] if '|' in words else words


def without_redirections(words):
    kept, skip = [], False
    for index, word in enumerate(words):
        following = words[index + 1] if index + 1 < len(words) else None
        if skip:
            skip = False
        elif word in REDIRECTIONS:
            skip = True
        elif not (word.isdecimal() and following in REDIRECTIONS):
            kept.append(word)
    return kept


def program_summary(command):
    """`btt_backup.zsh daily` for a cron line, instead of its paths, logs and interpreter flags."""
    try:
        words = without_redirections(command_words(command))
    except ValueError:
        return command
    while words and ASSIGNMENT.match(words[0]):
        words.pop(0)
    if words and words[0].rsplit('/', 1)[-1] in INTERPRETERS:
        words.pop(0)
        while words and words[0].startswith('-'):
            words.pop(0)
    if not words:
        return command
    return ' '.join([words[0].rsplit('/', 1)[-1], *words[1:]])


def validate_status(value):
    required = ('revision', 'uncommitted', 'jobs', 'live_only', 'repo_only', 'installable')
    object_fields(value, required, status=502)
    if not REVISION.fullmatch(field(value, 'revision', str)):
        raise ApiError(502, 'Invalid crontab revision')
    field(value, 'uncommitted', bool)
    field(value, 'installable', bool)
    for name in ('live_only', 'repo_only'):
        lines = field(value, name, list)
        if not all(isinstance(line, str) for line in lines):
            raise ApiError(502, f'Invalid {name} field')
    for job in field(value, 'jobs', list):
        validate_status_job(job)
    if value['installable'] != (not value['live_only']):
        raise ApiError(502, 'Inconsistent crontab installable state')
    return value


def validate_status_job(job):
    required = ('id', 'schedule_fields', 'command', 'paused', 'managed_block',
                'managed_switch', 'description', 'log_path')
    object_fields(job, required, status=502)
    if not JOB_ID.fullmatch(field(job, 'id', str)):
        raise ApiError(502, 'Invalid crontab job id')
    schedule = field(job, 'schedule_fields', list)
    if len(schedule) not in (1, 5) or not all(isinstance(item, str) and item for item in schedule):
        raise ApiError(502, 'Invalid crontab schedule fields')
    field(job, 'command', str)
    field(job, 'paused', bool)
    block = field(job, 'managed_block', str, nullable=True)
    switch = field(job, 'managed_switch', str, nullable=True)
    field(job, 'description', str)
    field(job, 'log_path', str, nullable=True)
    if switch is not None and block is None:
        raise ApiError(502, 'Managed switch has no block owner')


def display_log(path):
    if path is None:
        return None, None
    target = Path(path)
    if not target.is_absolute() or not safe_display_path(target):
        return None, None
    try:
        modified = target.stat().st_mtime
    except OSError:
        modified = None
    return str(target), modified


def display_job(job):
    expression = ' '.join(job['schedule_fields'])
    block, switch = job['managed_block'], job['managed_switch']
    if block:
        owner_kind = OwnerKind.MANAGED
        owner = f'{block} — {switch or "macbook/crontab"}'
    else:
        owner_kind, owner = OwnerKind.TRACKED, 'macbook/crontab'
    log_path, log_modified = display_log(job['log_path'])
    return CronJob(id=job['id'], expression=expression,
                   schedule=describe_schedule(expression), command=job['command'],
                   program=program_summary(job['command']),
                   description=job['description'], owner_kind=owner_kind, owner=owner,
                   log_path=log_path, log_modified=log_modified, paused=job['paused'],
                   managed_block=block, managed_switch=switch)


def cli_error(result):
    try:
        value = json.loads(result.stdout)
    except (TypeError, ValueError):
        value = None
    if isinstance(value, dict) and isinstance(value.get('error'), str):
        message = value['error']
        live_only = value.get('live_only')
        if isinstance(live_only, list) and all(isinstance(line, str) for line in live_only):
            message += '\nLive-only lines:\n' + '\n'.join(live_only)
        return redact(message)
    return redact((result.stderr or result.stdout).strip()) or f'Tool exited {result.returncode}'


class CrontabAdapter:
    def __init__(self, runner):
        self.runner = runner

    def read(self):
        value = validate_status(json_result(self.runner.run((*SYNC, 'status', '--json'))))
        live_only, repo_only = value['live_only'], value['repo_only']
        return CrontabPayload(
            revision=value['revision'], uncommitted=value['uncommitted'],
            jobs=[display_job(job) for job in value['jobs']],
            in_sync=not live_only and not repo_only, order_changed=False,
            live_only=live_only, repo_only=repo_only, installable=value['installable'])

    def mutate(self, body):
        object_fields(body, ('action', 'revision'), ('job', 'confirmed'))
        action = field(body, 'action', str, status=400)
        revision_value = field(body, 'revision', str, status=400)
        if action not in ACTIONS:
            raise ApiError(400, 'Unsupported crontab action')
        if not REVISION.fullmatch(revision_value):
            raise ApiError(400, 'Invalid crontab revision')
        argv = (*SYNC, action)
        if action == 'install':
            confirm(body)
        else:
            job = field(body, 'job', str, status=400)
            if not JOB_ID.fullmatch(job):
                raise ApiError(400, 'Invalid crontab job id')
            argv += (job,)
        result = self.runner.run((*argv, '--revision', revision_value, '--json'))
        if result.returncode in (3, 75):
            raise ApiError(409, cli_error(result))
        checked(result)
        value = json_result(result)
        object_fields(value, ('message', 'revision', 'uncommitted'), status=502)
        field(value, 'message', str)
        if not REVISION.fullmatch(field(value, 'revision', str)):
            raise ApiError(502, 'Invalid crontab revision')
        field(value, 'uncommitted', bool)
        return Reply(value)
