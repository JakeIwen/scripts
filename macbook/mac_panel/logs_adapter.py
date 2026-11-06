"""Log inventory and rotation relayed to macbook/scripts/log_rotate.py; sizes and paths only, never contents."""
import math
from pathlib import Path

from cli_runner import checked, json_result
from crontab_adapter import cli_error
from log_redaction import redact
from panel_contracts import ApiError, Level, LogsPayload, Reply
from panel_paths import LOG_ROTATE, LOG_ROTATE_LABEL
from wire_validation import confirm, field, object_fields

ACTIONS = frozenset(('rotate', 'policy', 'add', 'ignore', 'unignore'))
PATH_ACTIONS = ('policy', 'add', 'ignore', 'unignore')
# Exit codes of the rotator CLI beyond 0 (ok) and 1 (error).
EXIT_CONFLICT, EXIT_BUSY = 3, 75
READ_TIMEOUT, ROTATE_TIMEOUT = 60, 600
BYTES_PER_MB = 1024 * 1024
# A file this many times past its limit means rotation is failing, not merely due.
OVERSHOOT = 2
MAX_PATH = 4096
MAX_REVISION = 128
STATES = frozenset(('ok', 'due', 'disabled', 'missing', 'refused'))
POLICY_FLAGS = (('compress', '--compress'), ('enabled', '--enabled'))


def valid_path(value):
    return (isinstance(value, str) and 0 < len(value) <= MAX_PATH and Path(value).is_absolute()
            and not any(ord(char) < 32 for char in value))


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_policy(policy):
    object_fields(policy, ('max_mb', 'keep', 'compress', 'enabled', 'override'), status=502)
    field(policy, 'max_mb', float)
    field(policy, 'keep', int)
    for key in ('compress', 'enabled', 'override'):
        field(policy, key, bool)


def validate_entry(entry):
    object_fields(entry, ('path', 'source', 'bytes', 'archived_bytes', 'archives', 'mtime', 'growth_mb_per_day',
                          'policy', 'last_rotated', 'state'), status=502)
    if not valid_path(field(entry, 'path', str)):
        raise ApiError(502, 'Invalid log path')
    field(entry, 'source', str)
    for key in ('bytes', 'archived_bytes', 'archives'):
        field(entry, key, int)
    field(entry, 'growth_mb_per_day', float, nullable=True)
    for key in ('mtime', 'last_rotated'):
        field(entry, key, (int, float, str), nullable=True)
    if field(entry, 'state', str) not in STATES:
        raise ApiError(502, 'Invalid log state')
    field(entry, 'note', str, nullable=True)
    validate_policy(field(entry, 'policy', dict))


def validate_inventory(value):
    object_fields(value, ('revision', 'defaults', 'totals', 'logs', 'ignored'), status=502)
    if not 0 < len(field(value, 'revision', str)) <= MAX_REVISION:
        raise ApiError(502, 'Invalid log revision')
    object_fields(field(value, 'defaults', dict), ('max_mb', 'keep', 'compress', 'warn_total_mb'), status=502)
    totals = object_fields(field(value, 'totals', dict), ('files', 'bytes', 'archived_bytes'), status=502)
    for key in totals:
        field(totals, key, int)
    for entry in field(value, 'logs', list):
        validate_entry(entry)
    return value


def ignored_paths(items):
    """The rotator's ignore list as plain paths, whether it sends strings or {path} records."""
    paths = [item.get('path') if isinstance(item, dict) else item for item in items]
    if not all(valid_path(path) for path in paths):
        raise ApiError(502, 'Invalid ignored path')
    return paths


def overshoots(entry):
    policy = entry['policy']
    return policy['enabled'] and entry['bytes'] > OVERSHOOT * policy['max_mb'] * BYTES_PER_MB


def warnings(payload):
    """Why the section needs attention; the same rules the page's attention list applies."""
    found = []
    service = payload['service']
    if service.get('loaded') is False or service.get('installed') is False:
        found.append('rotator agent is not running')
    found += [f"{Path(entry['path']).name} is past {OVERSHOOT}x its limit" for entry in payload['logs'] if overshoots(entry)]
    found += [f"{Path(entry['path']).name} refused" for entry in payload['logs'] if entry['state'] == 'refused']
    if payload['totals']['bytes'] + payload['totals']['archived_bytes'] > payload['defaults']['warn_total_mb'] * BYTES_PER_MB:
        found.append('total log size is over its warning level')
    return found


def log_samples(payload):
    totals = payload['totals']
    text = f"{totals['bytes'] / BYTES_PER_MB:.1f} MB live, {totals['archived_bytes'] / BYTES_PER_MB:.1f} MB archived"
    return [('total', Level.WARN if warnings(payload) else Level.OK, totals['bytes'], text)]


def failure(result):
    """Exit 3 (stale revision) and 75 (rotator busy) are conflicts the reader can retry; 1 is a refusal."""
    if result.returncode == EXIT_BUSY:
        raise ApiError(409, 'The rotator is busy with another run; try again shortly', 'busy')
    if result.returncode == EXIT_CONFLICT:
        raise ApiError(409, cli_error(result), 'conflict')
    if result.returncode:
        raise ApiError(400, cli_error(result))


class LogsAdapter:
    def __init__(self, runner, services):
        self.runner = runner
        self.services = services

    def service_state(self):
        try:
            return self.services.agent_state(LOG_ROTATE_LABEL)
        except ApiError:
            return {'label': LOG_ROTATE_LABEL, 'installed': None, 'loaded': None, 'pid': None, 'last_exit': None}

    def read(self) -> LogsPayload:
        value = validate_inventory(json_result(self.runner.run((*LOG_ROTATE, 'inventory', '--json'), timeout=READ_TIMEOUT)))
        return LogsPayload(generated_at=value.get('generated_at'), revision=value['revision'],
                           defaults=value['defaults'], totals=value['totals'], logs=value['logs'],
                           ignored=ignored_paths(field(value, 'ignored', list)), service=self.service_state())

    def mutate(self, body):
        action = field(body, 'action', str, status=400) if isinstance(body, dict) else None
        if action not in ACTIONS:
            raise ApiError(400, 'Unsupported log action')
        if action == 'rotate':
            return self.rotate(body)
        return self.edit(action, body)

    def rotate(self, body):
        object_fields(body, ('action',), ('path', 'force', 'confirmed'))
        confirm(body)
        argv = [*LOG_ROTATE, 'rotate', '--json']
        if 'path' in body:
            if not valid_path(body['path']):
                raise ApiError(400, 'Invalid log path')
            argv += ['--only', body['path']]
        if 'force' in body:
            if field(body, 'force', bool, status=400):
                argv.append('--force')
        result = self.runner.run(tuple(argv), timeout=ROTATE_TIMEOUT)
        failure(result)
        value = json_result(checked(result))
        object_fields(value, ('rotated', 'skipped', 'errors'), status=502)
        rotated, skipped, errors = (field(value, key, list) for key in ('rotated', 'skipped', 'errors'))
        message = f'Rotated {len(rotated)}, skipped {len(skipped)}'
        if errors:
            message += f'; {len(errors)} failed: {redact(str(errors[0]))}'
        return Reply({'message': message, 'rotated': rotated, 'skipped': skipped, 'errors': errors})

    def policy_flags(self, body):
        flags = []
        if body.get('clear') is not None and field(body, 'clear', bool, status=400):
            flags.append('--clear')
        if 'max_mb' in body:
            if not number(body['max_mb']) or body['max_mb'] <= 0:
                raise ApiError(400, 'max_mb must be a positive number')
            flags += ['--max-mb', str(body['max_mb'])]
        if 'keep' in body:
            if type(body['keep']) is not int or body['keep'] < 0:
                raise ApiError(400, 'keep must be a whole number of at least 0')
            flags += ['--keep', str(body['keep'])]
        for key, flag in POLICY_FLAGS:
            if key in body:
                flags += [flag, 'on' if field(body, key, bool, status=400) else 'off']
        if not flags:
            raise ApiError(400, 'Policy needs a change or clear')
        if '--clear' in flags and len(flags) > 1:
            raise ApiError(400, 'Clear cannot be combined with other policy changes')
        return flags

    def edit(self, action, body):
        optional = ('max_mb', 'keep', 'compress', 'enabled', 'clear') if action == 'policy' else ()
        object_fields(body, ('action', 'revision', 'path'), (*optional, 'confirmed'))
        revision = field(body, 'revision', str, status=400)
        if not 0 < len(revision) <= MAX_REVISION:
            raise ApiError(400, 'Invalid log revision')
        if not valid_path(body['path']):
            raise ApiError(400, 'Invalid log path')
        flags = self.policy_flags(body) if action == 'policy' else []
        result = self.runner.run((*LOG_ROTATE, action, '--json', '--revision', revision, *flags, body['path']),
                                 timeout=READ_TIMEOUT)
        failure(result)
        value = json_result(checked(result))
        new_revision = field(value, 'revision', str) if isinstance(value, dict) else None
        if not new_revision:
            raise ApiError(502, 'Invalid log revision')
        return Reply({'message': f'{action.capitalize()} saved', 'revision': new_revision})
