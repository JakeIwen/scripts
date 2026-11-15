"""The llm-restore CLI owns its crontab block, snapshot state and saved session sets."""
import re

from cli_runner import checked, json_result
from panel_contracts import ApiError, Reply, RestorePayload, RestoreStatus, SavedSet
from panel_paths import RESTORE
from wire_validation import confirm, field, object_fields

# Conservative on purpose: the CLI accepts more, but a name is also a file name and an argv word.
SAVE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
SNAPSHOT_ACTIONS = frozenset(('preview', 'restore', 'save'))
# Reopening dozens of sessions as iTerm tabs outlasts the runner's default.
RESTORE_TIMEOUT = 120


def decode_restore(value) -> RestoreStatus:
    object_fields(value, ('enabled', 'installed', 'up_to_date', 'last_snapshot', 'sessions_open'), status=502)
    for key in ('enabled', 'installed', 'up_to_date'):
        field(value, key, bool)
    field(value, 'last_snapshot', float, nullable=True)
    field(value, 'sessions_open', int, nullable=True)
    return value


def decode_saved(value) -> list[SavedSet]:
    if not isinstance(value, list):
        raise ApiError(502, 'Invalid saved sets')
    for item in value:
        object_fields(item, ('name', 'path', 'saved_at', 'sessions'), status=502)
        field(item, 'name', str)
        field(item, 'path', str)
        field(item, 'saved_at', float)
        if field(item, 'sessions', int) < 0:
            raise ApiError(502, 'Invalid sessions field')
    return value


def report_text(result) -> str:
    return '\n'.join(part.strip() for part in (result.stdout, result.stderr) if part.strip())


class RestoreAdapter:
    def __init__(self, runner):
        self.runner = runner

    def saved(self) -> list[SavedSet]:
        return decode_saved(json_result(self.runner.run((*RESTORE, '--saved', '--json'))))

    def read(self) -> RestorePayload:
        status = decode_restore(json_result(self.runner.run((*RESTORE, '--cron', '--json'))))
        return {**status, 'saved': self.saved()}

    def toggle(self, body) -> RestoreStatus:
        object_fields(body, ('enabled',))
        enabled = field(body, 'enabled', bool, status=400)
        argv = (*RESTORE, '--cron', 'on' if enabled else 'off', '--json')
        return decode_restore(json_result(self.runner.run(argv)))

    def snapshots(self, body):
        """POST /api/llm-restore/snapshots. `target` is {"kind": "auto"} (the automatic restore list)
        or {"kind": "saved", "name": NAME}; only restore needs `confirmed: true`."""
        object_fields(body, ('action',), ('target', 'name', 'confirmed'))
        action = field(body, 'action', str, status=400)
        if action not in SNAPSHOT_ACTIONS:
            raise ApiError(400, 'Unsupported snapshot action')
        if action == 'save':
            return self.save(body)
        object_fields(body, ('action', 'target'), ('confirmed',))
        if action == 'restore':
            confirm(body)
        argv = (*RESTORE, *(('--dry-run',) if action == 'preview' else ()), *self.target_args(body['target']))
        result = checked(self.runner.run(argv, timeout=RESTORE_TIMEOUT if action == 'restore' else 30))
        return Reply({'message': 'Restore finished' if action == 'restore' else 'Preview ready',
                      'report': report_text(result) or 'Nothing to report.'})

    def target_args(self, target) -> tuple[str, ...]:
        object_fields(target, ('kind',), ('name',))
        kind = field(target, 'kind', str, status=400)
        if kind == 'auto':
            object_fields(target, ('kind',))
            return ()
        if kind != 'saved':
            raise ApiError(400, 'Unsupported snapshot target')
        name = field(target, 'name', str, status=400)
        match = next((item for item in self.saved() if item['name'] == name), None)
        if match is None:
            raise ApiError(400, 'Unknown saved set')
        # The listed path, not the name: the CLI strips a trailing `.sh` from a bare name, so only a
        # path (any argument with a `/`) always reaches the exact file that was listed.
        return ('--', match['path'])

    def save(self, body):
        object_fields(body, ('action',), ('name',))
        name = field(body, 'name', str, status=400) if 'name' in body else ''
        if name and (not SAVE_NAME.fullmatch(name) or name.endswith('.sh')):
            raise ApiError(400, 'Name must be letters, digits, . _ - (not ending in .sh) and start with a letter or digit (max 64)')
        result = self.runner.run((*RESTORE, f'--save={name}'))
        if result.returncode and 'already exists' in result.stderr:
            raise ApiError(409, 'A saved set with that name already exists; pick another name')
        checked(result)
        return Reply({'message': 'Snapshot saved', 'report': report_text(result)})
