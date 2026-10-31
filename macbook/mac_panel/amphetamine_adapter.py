"""Amphetamine owns native settings; the panel relays validated CLI requests."""
from cli_runner import json_result
from log_redaction import redact
from panel_contracts import ApiError
from panel_paths import PYTHON, ROOT
from wire_validation import field, object_fields


COMMAND = (str(PYTHON), '-B', str(ROOT / 'macbook/scripts/amphetamine_control.py'))
# amphetamine/cli.py exits 4 when macOS privacy settings refuse access.
PERMISSION_DENIED = 4


def payload(value):
    object_fields(value, ('enabled', 'delay_seconds', 'display_sleep_locked', 'revision',
                         'low_battery_enabled', 'low_battery_percent', 'session', 'error',
                         'minimum_minutes', 'maximum_minutes'), status=502)
    for name in ('enabled', 'display_sleep_locked', 'low_battery_enabled'):
        field(value, name, bool)
    for name in ('delay_seconds', 'low_battery_percent', 'minimum_minutes', 'maximum_minutes'):
        field(value, name, int)
    field(value, 'revision', str)
    field(value, 'error', str, nullable=True)
    session = field(value, 'session', dict, nullable=True)
    if session is not None:
        for name in ('running', 'active', 'display_sleep', 'screen_saver', 'closed_display'):
            field(session, name, bool)
        field(session, 'remaining', int)
    return value


def status_json(result):
    if result.returncode == PERMISSION_DENIED:
        # macOS attributes the panel's children to the binary launchd started, the
        # venv symlink's target. Resolve it only to name it; commands still run PYTHON.
        raise ApiError(502, f'{redact(result.stderr.strip())} For the Mac Panel that program is '
                            f'{PYTHON.resolve()} (Cmd-Shift-G pastes the path into the file '
                            'picker); restart the panel after granting it.')
    return payload(json_result(result))


class AmphetamineAdapter:
    def __init__(self, runner):
        self.runner = runner

    def read(self):
        return status_json(self.runner.run((*COMMAND, 'status')))

    def save(self, body):
        object_fields(body, ('enabled', 'minutes', 'revision'))
        enabled = field(body, 'enabled', bool, status=400)
        minutes = field(body, 'minutes', int, status=400)
        revision = field(body, 'revision', str, status=400)
        result = self.runner.run((*COMMAND, 'configure', '--enabled', 'on' if enabled else 'off',
                                  '--minutes', str(minutes), '--revision', revision), timeout=90)
        if result.returncode in (2, 3):
            raise ApiError(409 if result.returncode == 3 else 400, result.stderr.strip())
        return status_json(result)
