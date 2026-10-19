"""Explicit route table; adapters own each tool's CLI contract."""
from cli_runner import CliRunner
from crontab_adapter import CrontabAdapter
from deferred_adapter import DeferredAdapter
from guard_adapter import GuardAdapter
from ntfy_adapter import NtfyAdapter
from panel_contracts import ApiError, Reply, Section
from restore_adapter import RestoreAdapter
from services_adapter import ServicesAdapter
from settings_adapter import SettingsAdapter


class PanelRoutes:
    def __init__(self, runner=None, settings=None):
        runner = runner or CliRunner()
        self.deferred = DeferredAdapter(runner)
        self.guard = GuardAdapter(runner)
        self.adapters = {
            Section.SETTINGS: SettingsAdapter(settings),
            Section.DEFERRED: self.deferred,
            Section.SERVICES: ServicesAdapter(runner),
            Section.CRONTAB: CrontabAdapter(runner),
            Section.RESTORE: RestoreAdapter(runner),
            Section.GUARD: self.guard,
            Section.NTFY: NtfyAdapter(),
        }
        self.reads = {f'/api/{name.value}': adapter.read for name, adapter in self.adapters.items()}
        self.writes = {
            '/api/settings': self.adapters[Section.SETTINGS].save,
            '/api/deferred': self.deferred.mutate,
            '/api/services/restart': self.adapters[Section.SERVICES].restart,
            '/api/llm-restore': self.adapters[Section.RESTORE].toggle,
            '/api/guard': self.guard.action,
            '/api/ntfy/test': self.notify_test,
        }

    def notify_test(self, body):
        if set(body) != {'confirmed'}:
            raise ApiError(400, 'Only explicit confirmation is accepted')
        return self.guard.action({'action': 'notify-test', **body})

    def dispatch(self, method, path, body=None):
        if method == 'GET' and path.startswith('/api/tasks/'):
            return Reply(self.deferred.tasks.get(path.removeprefix('/api/tasks/')))
        routes = self.reads if method == 'GET' else self.writes
        if path not in routes:
            raise ApiError(404, 'Route not found')
        value = routes[path]() if method == 'GET' else routes[path](body)
        return value if isinstance(value, Reply) else Reply(value)

    def close(self):
        self.deferred.tasks.close()
