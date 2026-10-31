"""Serialize one-shot reconciliation, local edits and restricting config replay."""
from dataclasses import replace
import time

from shared.metered.policy import PolicyStore
from shared.metered.records import Actual, Handling, Host, HostState
from shared.metered.registry import consumers
from shared.metered.store import RecordStore
from shared.python import tool_settings
from .config import Paths, SETTINGS_TOOL
from .decisions import evaluate, leased
from .low_data_mode import LowDataMode
from .network import collect
from .records import LowDataSettings, MacStatus, empty_state
from .sync import Cache, PiTransport


class Controller:
    def __init__(self, paths=None, collector=collect, transport=None, low_data=None,
                 settings=tool_settings.read_state, clock=time.time):
        self.paths = paths or Paths.default()
        self.state = RecordStore(self.paths.file('state'), HostState, empty_state)
        self.policy = PolicyStore(Host.MAC, self.paths.policy)
        self.cache = Cache(self.paths, transport or PiTransport())
        self.collector, self.clock, self.settings = collector, clock, settings
        self.low_data = low_data or LowDataMode(self.paths)
        self.adapters = {'mac.low_data_mode': self.low_data.reconcile,
                         'mac.icloud': self._icloud}

    @staticmethod
    def _icloud(permitted, network, enabled, now, restore=False):
        return Actual.NA, 'Advisory only via Low Data Mode; iCloud may ignore the flag'

    def current(self):
        state = self.state.load()
        now = self.clock()
        if (state.host != Host.MAC or not leased(state, now) or
                state.config_revision != self.cache.config.load().revision or
                state.policy_revision != self.policy.load().revision):
            return empty_state()
        return state

    def status(self):
        settings = self.settings(SETTINGS_TOOL)
        registry = list(consumers().values())
        if settings.values['enabled'] and settings.error is None:
            registry = [replace(row, how=Handling.TICK,
                                note='Automatic constrain; owned flags require explicit restore')
                        if row.id == 'mac.low_data_mode' else row for row in registry]
        pi = self.cache.pi.load()
        return MacStatus(self.cache.overlay(), self.policy.load(), self.state.load(), registry,
                         pi if pi.evaluated_at else None, self.cache.pending.load().ops,
                         self.cache.status.load(), leased(self.current(), self.clock()),
                         self.cache.config.path.exists(),
                         LowDataSettings(settings.values['enabled'], settings.revision,
                                         settings.error)).as_dict()

    def _evaluate(self, network, previous):
        state = evaluate(network, self.cache.pi.load(), self.cache.overlay(),
                         self.cache.config.load(), self.policy.load(), previous, self.clock(),
                         self.cache.config.path.exists())
        policy = self.policy.expire(state.effective, self.clock())
        if state.policy_revision != policy.revision:
            state = evaluate(network, self.cache.pi.load(), self.cache.overlay(),
                             self.cache.config.load(), policy, previous, self.clock(),
                             self.cache.config.path.exists())
        return state

    def tick(self, force_sync=False, enforce=True, restore=False):
        with self.state.locked():
            try:
                previous = self.state.load()
            except (OSError, ValueError):
                previous = empty_state()
            network = self.collector()
            if force_sync or self.cache.due(network, self.clock()):
                self.cache.sync(previous, self.clock())
            state = self._evaluate(network, previous)
            if enforce:
                state = self._enforce(state, network, restore)
            self.state.save_locked(state)
            return state

    def _enforce(self, state, network, restore):
        settings = self.settings(SETTINGS_TOOL)
        enabled = settings.values['enabled'] and settings.error is None
        states = dict(state.consumers)
        if restore and (not enabled or not states['mac.low_data_mode'].permitted):
            raise ValueError('Restore requires enabled control and Low Data Mode permission first')
        for key, result in states.items():
            row = consumers()[key]
            actual = Actual.NA if row.how in (Handling.UNPROTECTED, Handling.ADVISORY) else Actual.UNKNOWN
            adapter = self.adapters.get(key)
            reason = result.reason
            if adapter:
                actual, detail = adapter(result.permitted, network, enabled, self.clock(), restore)
                reason += '; ' + detail
            states[key] = replace(result, actual=actual, reason=reason)
        return replace(state, consumers=states)

    def add_ssid(self, ssid):
        with self.state.locked():
            operation = self.cache.add(ssid)
            self.state.save_locked(replace(self.state.load(), valid_until=None))
        return {'queued': operation.as_dict(), 'pending_sync': True}

    def remove_ssid(self, ssid, revision):
        if revision is None:
            raise ValueError('SSID removal requires --revision')
        with self.state.locked():
            result = self.cache.remove(ssid, revision, self.state.load(), self.clock())
            self.state.save_locked(replace(self.state.load(), valid_until=None))
        return result.as_dict()

    def configure_low_data(self, enabled, revision):
        with self.state.locked():
            result = tool_settings.save(SETTINGS_TOOL, {'enabled': enabled}, revision)
            self.state.save_locked(replace(self.state.load(), valid_until=None))
        return result.as_dict()
