"""The llm-restore CLI owns its crontab block and snapshot state."""
from cli_runner import json_result
from panel_contracts import RestorePayload
from panel_paths import RESTORE
from wire_validation import field, object_fields


def decode_restore(value) -> RestorePayload:
    object_fields(value, ('enabled', 'installed', 'up_to_date', 'last_snapshot', 'sessions_open'), status=502)
    for key in ('enabled', 'installed', 'up_to_date'):
        field(value, key, bool)
    field(value, 'last_snapshot', float, nullable=True)
    field(value, 'sessions_open', int, nullable=True)
    return value


class RestoreAdapter:
    def __init__(self, runner):
        self.runner = runner

    def read(self):
        return decode_restore(json_result(self.runner.run((*RESTORE, '--cron', '--json'))))

    def toggle(self, body):
        object_fields(body, ('enabled',))
        enabled = field(body, 'enabled', bool, status=400)
        argv = (*RESTORE, '--cron', 'on' if enabled else 'off', '--json')
        return decode_restore(json_result(self.runner.run(argv)))
