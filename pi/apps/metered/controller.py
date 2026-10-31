"""Serialize host episodes and publish desired versus observed consumer state."""
from dataclasses import replace
import hashlib
import json
import time

from shared.metered.config import ConfigStore
from shared.metered.policy import PolicyStore
from shared.metered.records import (
    Actual, Effective, EffectiveSource, Handling, Host, HostState, MeteredStatus, SyncResponse,
)
from shared.metered.registry import consumer, consumers
from shared.metered.rules import EVIDENCE_TTL, dendelion_verdict, effective, permits, unknown
from shared.metered.store import RecordStore
from .config import Paths
from .probe import collect


def empty_state(host=Host.PI):
    return HostState(host, 0.0, unknown('no evaluation available'),
                     Effective(True, EffectiveSource.VERDICT, None), {})


class Controller:
    def __init__(self, paths=None, collector=collect, adapters=None, clock=time.time):
        self.paths = paths or Paths.default()
        self.config = ConfigStore(self.paths.shared)
        self.policy = PolicyStore(Host.PI, self.paths.policy)
        self.state = RecordStore(self.paths.state, HostState, empty_state)
        self.mac_report = RecordStore(self.paths.mac_report, HostState, lambda: empty_state(Host.MAC))
        self.collector, self.adapters, self.clock = collector, adapters or {}, clock

    def status(self):
        report = self.mac_report.load()
        return MeteredStatus(self.config.load(), self.policy.load(), self.state.load(),
                             list(consumers().values()), report if report.evaluated_at else None).as_dict()

    def _evaluate(self, previous):
        config, policy = self.config.load(), self.policy.load()
        evidence = self.collector(config.home_network)
        now = self.clock()
        verdict = dendelion_verdict(evidence, config, now)
        current = effective(verdict, policy, now, previous.effective)
        policy = self.policy.expire(current, now)
        states = {key: permits(key, current, policy, now) for key, row in consumers().items()
                  if row.host == Host.PI}
        routing = [evidence.policy, sorted((member.name, member.percent, member.ssid_verified)
                   for member in evidence.members if member.percent > 0)]
        signature = hashlib.sha256(json.dumps(routing).encode()).hexdigest()
        deadlines = [now + EVIDENCE_TTL]
        if current.source == EffectiveSource.VERDICT and verdict.metered is False:
            deadlines.append(evidence.collected_at + EVIDENCE_TTL)
        if policy.mode.until is not None:
            deadlines.append(policy.mode.until)
        deadlines.extend(value.until for value in policy.approvals.values() if value.until is not None)
        return HostState(Host.PI, now, verdict, current, states,
                         config.revision, policy.revision, signature, min(deadlines))

    def evaluate(self, enforce=False):
        with self.state.locked():
            try:
                previous = self.state.load()
            except (OSError, ValueError):
                previous = empty_state()
            try:
                state = self._evaluate(previous)
            except (OSError, ValueError, TypeError):
                now = self.clock()
                verdict = unknown('metering configuration or evidence is invalid')
                policy = self.policy.default()
                current = effective(verdict, policy, now, previous.effective)
                states = {key: permits(key, current, policy, now) for key, row in consumers().items()
                          if row.host == Host.PI}
                state = HostState(Host.PI, now, verdict, current, states, -1, -1)
            if enforce:
                changed = previous.route_signature != state.route_signature
                state = self._enforce(state, changed)
                if state.consumers['pi.torrents'].actual == Actual.UNKNOWN:
                    state = replace(state, route_signature=previous.route_signature)
            else:
                state = replace(state, route_signature=previous.route_signature)
            self.state.save_locked(state)
            return state

    def _enforce(self, state, route_changed):
        states = dict(state.consumers)
        for key, row in consumers().items():
            if row.host != Host.PI:
                continue
            actual = Actual.NA if row.how in (Handling.UNPROTECTED, Handling.ADVISORY) else Actual.UNKNOWN
            if row.how == Handling.TICK:
                adapter = self.adapters.get(key)
                if adapter is not None:
                    try:
                        actual = adapter(states[key].permitted, reset_connections=route_changed)
                    except (OSError, ValueError, RuntimeError):
                        actual = Actual.UNKNOWN
            states[key] = replace(states[key], actual=actual)
        return replace(state, consumers=states)

    def current(self):
        try:
            state = self.state.load()
            now = self.clock()
            current = (0 <= now - state.evaluated_at <= EVIDENCE_TTL and
                       state.valid_until is not None and now < state.valid_until and
                       state.config_revision == self.config.load().revision and
                       state.policy_revision == self.policy.load().revision)
            if current:
                policy = self.policy.load()
                # Expiry is a deadline, not a configuration change.
                desired = effective(state.verdict, policy, now, state.effective)
                states = {key: permits(key, desired, policy, now) for key in state.consumers}
                return replace(state, effective=desired, consumers=states)
        except (OSError, ValueError):
            pass
        return self.evaluate()

    def sync(self, request):
        report = request.mac_report
        if report is not None:
            for key in report.consumers:
                consumer(key, Host.MAC)
        config, applied = self.config.apply_ops(request.ops)
        # Telemetry may be old after sleep; it must not block acknowledgement of
        # independently durable restricting edits or replace a newer report.
        if report is not None and 0 <= self.clock() - report.evaluated_at <= 300:
            with self.mac_report.locked():
                previous = self.mac_report.load()
                if report.evaluated_at >= previous.evaluated_at:
                    self.mac_report.save_locked(report)
        return SyncResponse(config, self.current(), applied)
