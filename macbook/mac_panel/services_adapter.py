"""Read launchd inventory; only freshly discovered user agents can restart."""
import os
from pathlib import Path
import plistlib
import re

from cli_runner import checked
from log_redaction import log_stat, log_tail, redact, safe_display_path
from panel_contracts import (ApiError, LogStream, ServiceKind, ServiceLogsPayload, ServiceRecord,
                             ServicesPayload)
from panel_paths import HOME
from service_configuration import parse_enabled
from wire_validation import confirm, field, object_fields

LABEL_PATTERN = re.compile(r'^com\.jacobr\.[A-Za-z0-9_.-]+$')
LOG_KEYS = (('StandardOutPath', LogStream.STDOUT), ('StandardErrorPath', LogStream.STDERR))
# A plist pointing a stream at /dev/null has chosen to keep no log; the page should not list one.
UNUSED_LOG = '/dev/null'
# launchctl print's answer for a daemon plist that is installed but not bootstrapped (or booted out).
NOT_LOADED = 'Could not find service'


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


def plist_logs(path, label):
    """The (stream, path) pairs an owned plist declares, or the reason it cannot be trusted."""
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
        for key, stream in LOG_KEYS:
            value = config.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f'Invalid {key}')
            if value and value != UNUSED_LOG and value not in seen:
                seen.add(value)
                logs.append((stream, value))
        return logs, None
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        return [], str(exc)


class ServicesAdapter:
    def __init__(self, runner, agents=None, daemons=None, uid=None):
        self.runner = runner
        self.agents = agents if agents is not None else HOME / 'Library/LaunchAgents'
        self.daemons = daemons if daemons is not None else Path('/Library/LaunchDaemons')
        self.uid = os.getuid() if uid is None else uid

    def owned_plists(self, directory):
        return {p.stem: p for p in sorted(directory.glob('com.jacobr.*.plist'))
                if LABEL_PATTERN.fullmatch(p.stem)}

    def discover_agents(self):
        listed = parse_list(checked(self.runner.run(('/bin/launchctl', 'list'))).stdout)
        return listed, self.owned_plists(self.agents)

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
        for label, path in self.owned_plists(self.daemons).items():
            result = self.runner.run(('/bin/launchctl', 'print', f'system/{label}'))
            if result.returncode == 0:
                rows.append(self.row(label, ServiceKind.DAEMON, path, loaded=True, **parse_print(result.stdout)))
                continue
            # launchctl says why on stderr; any other failure leaves the state unknown.
            detail = (result.stderr or result.stdout).strip()
            if NOT_LOADED in detail:
                rows.append(self.row(label, ServiceKind.DAEMON, path, loaded=False,
                                     state='not loaded', pid=None, last_exit=None))
                continue
            reason = redact(detail) or f'launchctl exited {result.returncode}'
            rows.append(self.row(label, ServiceKind.DAEMON, path, reason, loaded=None,
                                 state='unknown', pid=None, last_exit=None))
        self.mark_disabled(rows)
        return {'services': rows}

    def mark_disabled(self, rows):
        # The local-service `stop` verb disables a job so KeepAlive cannot undo it: a choice, not a fault.
        domains = {ServiceKind.AGENT: f'gui/{self.uid}', ServiceKind.DAEMON: 'system'}
        listings = {}
        for row in rows:
            if row['loaded'] is not False:
                continue
            if row['kind'] not in listings:
                result = self.runner.run(('/bin/launchctl', 'print-disabled', domains[row['kind']]))
                listings[row['kind']] = result.stdout if result.returncode == 0 else ''
            try:
                row['disabled'] = not parse_enabled(listings[row['kind']], row['label'])
            except RuntimeError:
                # Unreadable output leaves the plain "not loaded" warning in place.
                row['disabled'] = False

    def row(self, label, kind, path, reason=None, **values) -> ServiceRecord:
        declared, error = plist_logs(path, label)
        return ServiceRecord(label=label, kind=kind, plist=str(path) if path else None,
                             logs=[log_stat(value, stream) for stream, value in declared],
                             error=error or reason, disabled=False, **values)

    def logs(self, label) -> ServiceLogsPayload:
        if not LABEL_PATTERN.fullmatch(label):
            raise ApiError(400, 'Invalid service label')
        path = self.owned_plists(self.agents).get(label) or self.owned_plists(self.daemons).get(label)
        if path is None:
            raise ApiError(404, 'No owned plist declares logs for this service')
        declared, error = plist_logs(path, label)
        if error:
            raise ApiError(502, redact(error))
        return {'label': label, 'logs': [log_tail(value, stream) for stream, value in declared]}

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
