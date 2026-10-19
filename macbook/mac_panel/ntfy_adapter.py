"""Alert routing metadata only; credential values and settings env are excluded."""
import json
from pathlib import Path
import shlex
import tomllib

from log_redaction import redact_data
from panel_contracts import AlertRecord, NtfyPayload
from panel_paths import HOME, ROOT


def notifier_path(command):
    if not isinstance(command, str):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    return next((token for token in tokens if Path(token).name == 'claude_ntfy_notify.py'), None)


def claude_alerts(path):
    settings = json.loads(path.read_text())
    if not isinstance(settings, dict) or not isinstance(settings.get('hooks', {}), dict):
        raise ValueError('Invalid Claude hook configuration')
    alerts = []
    # Deliberately select hooks only: settings.env may hold unrelated credentials.
    for event, entries in settings.get('hooks', {}).items():
        if not isinstance(entries, list):
            raise ValueError('Invalid Claude hook entries')
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('hooks', []), list):
                raise ValueError('Invalid Claude hook entry')
            for hook in entry.get('hooks', []):
                if not isinstance(hook, dict):
                    raise ValueError('Invalid Claude hook')
                script = notifier_path(hook.get('command'))
                if not script:
                    continue
                matcher = entry.get('matcher', '')
                if not isinstance(matcher, str):
                    raise ValueError('Invalid hook matcher')
                alerts.append(AlertRecord(source='Claude Code', event=event, matcher=matcher,
                    script=script, destination='vanpi ntfy_send.sh over SSH (agent topic)',
                    configuration=str(path)))
    return alerts


def codex_alert(path):
    value = tomllib.loads(path.read_text()).get('notify')
    if value is not None and (not isinstance(value, list) or
                              not all(isinstance(item, str) for item in value)):
        raise ValueError('Invalid Codex notify configuration')
    script = value[0] if value else ''
    if script and Path(script).name in ('python3', 'python', 'bash', 'zsh'):
        script = next((item for item in value[1:] if not item.startswith('-')), script)
    return AlertRecord(source='Codex', event='Configured notify hook' if value else 'Off',
        matcher='', script=script, destination='Agent notification route' if value else 'Off',
        configuration=str(path))


class NtfyAdapter:
    def __init__(self, claude_path=None, codex_path=None):
        self.claude_path = claude_path if claude_path is not None else HOME / '.claude/settings.json'
        self.codex_path = codex_path if codex_path is not None else HOME / '.codex/config.toml'

    def read(self) -> NtfyPayload:
        alerts, errors = [], []
        for source, reader, path in (('Claude Code', claude_alerts, self.claude_path),
                                      ('Codex', codex_alert, self.codex_path)):
            try:
                value = reader(path)
                alerts.extend(value if isinstance(value, list) else [value])
            except (OSError, ValueError):
                errors.append(f'{source}: configuration is missing or unreadable')
        sender = str(ROOT / 'macbook/scripts/ntfy_warning.py')
        destination = 'Shared warning destination (value hidden)'
        configuration = 'pi/secrets/.bash_variables (not read by this panel)'
        for source, event in (
            ('Deferred jobs', 'Failure, give-up, invalid-job and checker-stopped warnings'),
            ('BTT guard', 'Monitor warnings'),
        ):
            alerts.append(AlertRecord(source=source, event=event, matcher='', script=sender,
                                      destination=destination, configuration=configuration))
        return redact_data({'alerts': alerts, 'errors': errors})
