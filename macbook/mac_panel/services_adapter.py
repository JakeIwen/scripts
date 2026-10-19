"""Read launchd inventory; only freshly discovered user agents can restart."""
import os
from pathlib import Path
import plistlib
import re

from cli_runner import checked
from log_redaction import log_tail, safe_display_path
from panel_contracts import ApiError, ServiceKind, ServiceRecord, ServicesPayload
from panel_paths import HOME
from wire_validation import confirm, field, object_fields

LABEL_PATTERN = re.compile(r'^com\.jacobr\.[A-Za-z0-9_.-]+$')


def parse_list(text):
    jobs = {}
    for line in text.splitlines():
        columns = line.split()
        if len(columns) != 3 or not LABEL_PATTERN.fullmatch(columns[2]):
            continue
        pid, status, label = columns
        if (pid != '-' and not pid.isdecimal()) or not re.fullmatch(r'-?\d+', status):
            raise ApiError(502, 'Invalid launchctl list row')
        jobs[label] = {'pid': None if pid == '-' else int(pid), 'last_exit': int(status)}
    return jobs


def parse_print(text):
    def number(key):
        match = re.search(r'^\s*' + re.escape(key) + r' = (-?\d+)\s*$', text, re.M)
        return int(match[1]) if match else None
    state = re.search(r'^\s*state = ([^\r\n]+)', text, re.M)
    return {'state': state[1].strip() if state else 'unknown',
            'pid': number('pid'), 'last_exit': number('last exit code')}


def plist_details(path, label):
    if path is None:
        return [], None
    try:
        if not safe_display_path(path):
            raise ValueError('Plist path is not permitted')
        config = plistlib.loads(path.read_bytes())
        if not isinstance(config, dict) or config.get('Label') != label:
            raise ValueError('Plist label does not match its filename')
        logs = []
        seen = set()
        for key in ('StandardOutPath', 'StandardErrorPath'):
            value = config.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f'Invalid {key}')
            if value and value not in seen:
                seen.add(value)
                logs.append(log_tail(value))
        return logs, None
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        return [], str(exc)


class ServicesAdapter:
    def __init__(self, runner, agents=None, daemons=None, uid=None):
        self.runner = runner
        self.agents = agents if agents is not None else HOME / 'Library/LaunchAgents'
        self.daemons = daemons if daemons is not None else Path('/Library/LaunchDaemons')
        self.uid = os.getuid() if uid is None else uid

    def discover_agents(self):
        listed = parse_list(checked(self.runner.run(('/bin/launchctl', 'list'))).stdout)
        plists = {p.stem: p for p in self.agents.glob('com.jacobr.*.plist')
                  if LABEL_PATTERN.fullmatch(p.stem)}
        return listed, plists

    def read(self) -> ServicesPayload:
        listed, plists = self.discover_agents()
        rows = []
        for label in sorted(listed.keys() | plists.keys()):
            job = listed.get(label)
            rows.append(self.row(label, ServiceKind.AGENT, plists.get(label),
                                 loaded=job is not None,
                                 state='running' if job and job['pid'] else 'not running',
                                 pid=job['pid'] if job else None,
                                 last_exit=job['last_exit'] if job else None))
        for path in sorted(self.daemons.glob('com.jacobr.*.plist')):
            if not LABEL_PATTERN.fullmatch(path.stem):
                continue
            result = self.runner.run(('/bin/launchctl', 'print', f'system/{path.stem}'))
            values = parse_print(result.stdout) if result.returncode == 0 else {
                'state': 'unknown', 'pid': None, 'last_exit': None}
            rows.append(self.row(path.stem, ServiceKind.DAEMON, path,
                                 loaded=True if result.returncode == 0 else None, **values))
        return {'services': rows}

    def row(self, label, kind, path, **values) -> ServiceRecord:
        logs, error = plist_details(path, label)
        return ServiceRecord(label=label, kind=kind, plist=str(path) if path else None,
                             logs=logs, error=error, **values)

    def restart(self, body):
        object_fields(body, ('label', 'confirmed'))
        confirm(body)
        label = field(body, 'label', str, status=400)
        if not LABEL_PATTERN.fullmatch(label):
            raise ApiError(400, 'Invalid agent label')
        listed, plists = self.discover_agents()
        if label not in listed and label not in plists:
            raise ApiError(403, 'Only a freshly discovered user LaunchAgent can restart')
        checked(self.runner.run(('/bin/launchctl', 'kickstart', '-k', f'gui/{self.uid}/{label}')))
        return {'message': 'Restart requested'}
