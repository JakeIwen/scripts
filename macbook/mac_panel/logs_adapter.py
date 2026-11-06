"""Log inventory and rotation relayed to macbook/scripts/log_rotate.py; sizes and paths only, never contents."""
import json
import logging
from pathlib import Path
import threading
import time

from cli_runner import checked, json_result
from crontab_adapter import cli_error
from log_redaction import redact
from panel_contracts import ApiError, Level, LogsPayload, Reply
from panel_paths import LOG_ROTATE, LOG_ROTATE_ENV, LOG_ROTATE_LABEL
from wire_validation import confirm, field, object_fields

ACTIONS = frozenset(('rotate', 'policy', 'add', 'ignore', 'unignore', 'adopt'))
PATH_ACTIONS = ('policy', 'add', 'ignore', 'unignore', 'adopt')
LOG = logging.getLogger(__name__)
# Exit codes of the rotator CLI beyond 0 (ok) and 1 (error).
EXIT_CONFLICT, EXIT_BUSY = 3, 75
READ_TIMEOUT, ROTATE_TIMEOUT = 60, 600
REFRESH_TIMEOUT = 30
BYTES_PER_MB = 1024 * 1024
MAX_PATH = 4096
MAX_REVISION = 128
STATES = frozenset(('ok', 'due', 'disabled', 'missing', 'refused'))
PROBLEM_LEVELS = frozenset((Level.WARN.value, Level.ERROR.value))
POLICY_FLAGS = (('compress', '--compress'), ('enabled', '--enabled'))


def valid_path(value):
    return (isinstance(value, str) and 0 < len(value) <= MAX_PATH and Path(value).is_absolute()
            and not any(ord(char) < 32 for char in value))


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
    field(entry, 'reason', str, nullable=True)
    validate_policy(field(entry, 'policy', dict))


def validate_problem(problem):
    object_fields(problem, ('level', 'text'), status=502)
    if field(problem, 'level', str) not in PROBLEM_LEVELS:
        raise ApiError(502, 'Invalid problem level')
    field(problem, 'text', str)
    if field(problem, 'path', str, nullable=True) is not None and not valid_path(problem['path']):
        raise ApiError(502, 'Invalid problem path')


def validate_inventory(value):
    object_fields(value, ('revision', 'defaults', 'totals', 'logs', 'ignored', 'problems'), status=502)
    if not 0 < len(field(value, 'revision', str)) <= MAX_REVISION:
        raise ApiError(502, 'Invalid log revision')
    object_fields(field(value, 'defaults', dict), ('max_mb', 'keep', 'compress', 'warn_total_mb'), status=502)
    totals = object_fields(field(value, 'totals', dict), ('files', 'bytes', 'archived_bytes'), status=502)
    for key in totals:
        field(totals, key, int)
    for entry in field(value, 'logs', list):
        validate_entry(entry)
    for problem in field(value, 'problems', list):
        validate_problem(problem)
    return value


def ignored_paths(items):
    """The rotator's ignore list as plain paths, whether it sends strings or {path} records."""
    paths = [item.get('path') if isinstance(item, dict) else item for item in items]
    if not all(valid_path(path) for path in paths):
        raise ApiError(502, 'Invalid ignored path')
    return paths


def agent_problems(service):
    """The one fact the rotator cannot report about itself: whether launchd is running it."""
    if service.get('installed') is False:
        return [{'level': Level.WARN.value, 'text': 'rotator agent is not installed'}]
    if service.get('loaded') is False:
        return [{'level': Level.WARN.value, 'text': 'rotator agent is not running'}]
    return []


def log_samples(payload):
    """Live plus archived bytes, with both sizes after any worst-problem text."""
    problems = payload['problems']
    worst = next((item for item in problems if item['level'] == Level.ERROR.value), problems[0] if problems else None)
    level = Level.ERROR if worst and worst['level'] == Level.ERROR.value else Level.WARN if worst else Level.OK
    live, archived = payload['totals']['bytes'], payload['totals']['archived_bytes']
    sizes = f"{live / BYTES_PER_MB:g} MB live, {archived / BYTES_PER_MB:g} MB archived"
    return [('total', level, live + archived, f"{worst['text']}; {sizes}" if worst else sizes)]


def rotate_result(result):
    """The rotator exits 1 when any log failed yet still prints the full result: that is a result, not a failure."""
    if result.returncode != 1:
        return None
    try:
        value = json.loads(result.stdout)
    except ValueError:
        return None
    keys = ('rotated', 'skipped', 'errors')
    return value if isinstance(value, dict) and all(isinstance(value.get(key), list) for key in keys) else None


def error_text(item):
    """One rotation error as `name: reason`; the rotator sends {path, error}."""
    if isinstance(item, dict):
        return redact(f"{Path(str(item.get('path', ''))).name}: {item.get('error', '')}".strip(': '))
    return redact(str(item))


def failure(result):
    """Exit 3 (stale revision) and 75 (rotator busy) are conflicts the reader can retry; 1 is a refusal."""
    if result.returncode == EXIT_BUSY:
        raise ApiError(409, 'The rotator is busy with another run; try again shortly', 'busy')
    if result.returncode == EXIT_CONFLICT:
        raise ApiError(409, cli_error(result), 'conflict')
    if result.returncode:
        raise ApiError(400, cli_error(result))


class LogsAdapter:
    def __init__(self, runner, services, clock=time.monotonic):
        self.runner = runner
        self.services = services
        self.clock = clock
        self.cache_lock = threading.Lock()
        self.cached = None
        self.cached_at = None
        self.refreshing = False
        self.sampled_at = None
        self.refresh_thread = None

    def run(self, argv, timeout):
        return self.runner.run(tuple(argv), timeout=timeout, env=LOG_ROTATE_ENV)

    def service_state(self):
        try:
            return self.services.agent_state(LOG_ROTATE_LABEL)
        except ApiError:
            return {'label': LOG_ROTATE_LABEL, 'installed': None, 'loaded': None, 'pid': None, 'last_exit': None}

    def read(self, timeout=READ_TIMEOUT) -> LogsPayload:
        value = validate_inventory(json_result(self.run((*LOG_ROTATE, 'inventory', '--json'), timeout)))
        service = self.service_state()
        payload = LogsPayload(generated_at=value.get('generated_at'), revision=value['revision'],
                           defaults=value['defaults'], totals=value['totals'], logs=value['logs'],
                           ignored=ignored_paths(field(value, 'ignored', list)), service=service,
                           problems=[*agent_problems(service), *value['problems']])
        with self.cache_lock:
            self.cached, self.cached_at = payload, self.clock()
        return payload

    def sample(self, cadence):
        """Sample each cached inventory once; refresh without consuming a shared task slot."""
        with self.cache_lock:
            stale = self.cached is None or self.clock() - self.cached_at >= cadence
            payload = self.cached if self.cached_at != self.sampled_at else None
            # Prepare the next cadence's inventory as soon as this one is consumed.
            refresh = (stale or payload is not None) and not self.refreshing
            if refresh:
                self.refreshing = True
            if payload is not None:
                self.sampled_at = self.cached_at
        if refresh:
            try:
                self.refresh_thread = threading.Thread(target=self.refresh_inventory,
                                                       name='panel-log-inventory', daemon=True)
                self.refresh_thread.start()
            except Exception:
                with self.cache_lock:
                    self.refreshing = False
                raise
        return payload

    def refresh_inventory(self):
        try:
            self.read(timeout=REFRESH_TIMEOUT)
        except Exception:
            LOG.exception('Log inventory refresh failed')
        finally:
            with self.cache_lock:
                self.refreshing = False

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
        result = self.run(argv, ROTATE_TIMEOUT)
        value = rotate_result(result)
        if value is None:
            failure(result)
            value = json_result(checked(result))
        object_fields(value, ('rotated', 'skipped', 'errors'), status=502)
        rotated, skipped, errors = (field(value, key, list) for key in ('rotated', 'skipped', 'errors'))
        message = f'Rotated {len(rotated)}, skipped {len(skipped)}'
        if errors:
            message += f'; {len(errors)} failed: {error_text(errors[0])}'
        return Reply({'message': message, 'rotated': rotated, 'skipped': skipped, 'errors': errors})

    def policy_flags(self, body):
        flags = []
        if body.get('clear') is not None and field(body, 'clear', bool, status=400):
            flags.append('--clear')
        if 'max_mb' in body:
            if type(body['max_mb']) is not int or body['max_mb'] < 1:
                raise ApiError(400, 'max_mb must be a whole number of at least 1')
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
        if action == 'adopt' or (action == 'policy' and body.get('keep') == 0):
            confirm(body)
        result = self.run((*LOG_ROTATE, action, '--json', '--revision', revision, *flags, body['path']), READ_TIMEOUT)
        failure(result)
        value = json_result(checked(result))
        # The edit answers with the whole inventory plus the touched entry; decode it all so a bad reply fails here.
        object_fields(validate_inventory(value), ('log',), status=502)
        entry = field(value, 'log', dict, nullable=True)
        if entry is not None:
            validate_entry(entry)
        return Reply({'message': f'{action.capitalize()} saved', 'revision': value['revision'], 'log': entry})
