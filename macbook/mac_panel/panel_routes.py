"""Explicit route table; adapters own each tool's CLI contract."""
import math
from urllib.parse import parse_qs, urlsplit

from history_store import HistoryStore
from usage_adapter import UsageAdapter
from amphetamine_adapter import AmphetamineAdapter
from automations_adapter import AutomationsAdapter
from cli_runner import CliRunner
from background_tasks import BackgroundTasks
from crontab_adapter import CrontabAdapter
from deferred_adapter import DeferredAdapter
from guard_adapter import GuardAdapter
from logs_adapter import LogsAdapter
from metered_adapter import MeteredAdapter
from ntfy_adapter import NtfyAdapter
from panel_contracts import ApiError, Reply, Section
from restore_adapter import RestoreAdapter
from services_adapter import ServicesAdapter
from settings_adapter import SettingsAdapter


class PanelRoutes:
    def __init__(self, runner=None, settings=None, history=None):
        self.history = history if history is not None else HistoryStore()
        runner = runner or CliRunner()
        self.tasks = BackgroundTasks()
        self.automations = AutomationsAdapter(runner, tasks=self.tasks)
        self.deferred = DeferredAdapter(runner, tasks=self.tasks)
        self.guard = GuardAdapter(runner)
        self.services = ServicesAdapter(runner)
        self.logs = LogsAdapter(runner, self.services, tasks=self.tasks)
        self.adapters = {
            Section.METERED: MeteredAdapter(runner),
            Section.AMPHETAMINE: AmphetamineAdapter(runner),
            Section.SETTINGS: SettingsAdapter(settings),
            Section.AUTOMATIONS: self.automations,
            Section.DEFERRED: self.deferred,
            Section.SERVICES: self.services,
            Section.CRONTAB: CrontabAdapter(runner, tasks=self.tasks),
            Section.RESTORE: RestoreAdapter(runner),
            Section.GUARD: self.guard,
            Section.NTFY: NtfyAdapter(),
            Section.USAGE: UsageAdapter(runner),
            Section.LOGS: self.logs,
        }
        self.reads = {f'/api/{name.value}': adapter.read for name, adapter in self.adapters.items()}
        self.writes = {
            '/api/metered': self.adapters[Section.METERED].mutate,
            '/api/amphetamine': self.adapters[Section.AMPHETAMINE].save,
            '/api/settings': self.adapters[Section.SETTINGS].save,
            '/api/automations': self.automations.action,
            '/api/deferred': self.deferred.mutate,
            '/api/crontab': self.adapters[Section.CRONTAB].mutate,
            '/api/services': self.services.mutate,
            '/api/services/restart': self.adapters[Section.SERVICES].restart,
            '/api/llm-restore': self.adapters[Section.RESTORE].toggle,
            '/api/guard': self.guard.action,
            '/api/logs': self.logs.mutate,
            '/api/ntfy/test': self.notify_test,
        }

    def notify_test(self, body):
        from wire_validation import confirm
        confirm(body)
        if set(body) != {'confirmed'}:
            raise ApiError(400, 'Only explicit confirmation is accepted')
        return self.guard.action({'action': 'notify-test', **body})

    def history_read(self, query):
        fields = parse_qs(query, keep_blank_values=True)
        if (not {'section', 'since', 'until'} <= fields.keys() or
                fields.keys() - {'section', 'since', 'until', 'key'} or
                any(len(values) != 1 for values in fields.values())):
            raise ApiError(400, 'History requires section, since, until and optional key')
        try:
            section = Section(fields['section'][0])
            since, until = float(fields['since'][0]), float(fields['until'][0])
            if not math.isfinite(since) or not math.isfinite(until) or since > until:
                raise ValueError('Invalid interval')
        except ValueError:
            raise ApiError(400, 'Invalid history section or interval') from None
        from sampler import CADENCES
        if section not in CADENCES:
            raise ApiError(400, 'Section has no history')
        key = fields.get('key', [None])[0]
        if key == '':
            raise ApiError(400, 'History key must not be empty')
        return self.history.series(section, since, until, key=key)

    def dispatch(self, method, path, body=None):
        parsed = urlsplit(path)
        path = parsed.path
        if method == 'GET' and path == '/api/history':
            return Reply(self.history_read(parsed.query))
        if method == 'GET' and path.startswith('/api/tasks/'):
            return Reply(self.tasks.get(path.removeprefix('/api/tasks/')))
        if method == 'GET' and path.startswith('/api/services/logs/'):
            return Reply(self.services.logs(path.removeprefix('/api/services/logs/')))
        routes = self.reads if method == 'GET' else self.writes
        if path not in routes:
            raise ApiError(404, 'Route not found')
        value = routes[path]() if method == 'GET' else routes[path](body)
        return value if isinstance(value, Reply) else Reply(value)

    def close(self):
        self.tasks.close()
