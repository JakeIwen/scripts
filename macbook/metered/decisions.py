"""Pure Mac decision/lease calculation; Pi telemetry is never a local approval."""
from dataclasses import replace

from shared.metered.records import Host, HostState, MacNetwork
from shared.metered.registry import consumers
from shared.metered.rules import EVIDENCE_TTL, effective, mac_verdict, permits, unknown
from .config import HOME_GRACE, STATE_TTL


def leased(state, now):
    return (state is not None and 0 <= now - state.evaluated_at <= STATE_TTL and
            state.valid_until is not None and now < state.valid_until)


def home_verdict(pi, config, authoritative, now):
    if (pi.host != Host.PI or pi.evaluated_at > now or
            pi.valid_until is None or pi.config_revision != config.revision or
            pi.verdict.evidence_at is None or
            not 0 <= now - pi.verdict.evidence_at <= HOME_GRACE):
        return unknown('Pi uplink state is missing or stale')
    if set(config.metered_ssids) - set(authoritative.metered_ssids):
        return unknown('Pi has not evaluated pending metered SSIDs')
    if pi.verdict.metered is None:
        return pi.verdict
    age = now - pi.verdict.evidence_at
    if not leased(pi, now) or age > EVIDENCE_TTL:
        return replace(pi.verdict, reason=pi.verdict.reason +
                       f' (Pi evidence {age:.0f}s old, within grace)')
    return pi.verdict


def evaluate(network, pi, config, authoritative, policy, previous, now, cached):
    remote = home_verdict(pi, config, authoritative, now)
    mac = MacNetwork(network.ssid, network.gateway, network.gateway_mac, network.connected)
    verdict = mac_verdict(mac, remote, config)
    if not cached and verdict.metered is not True:
        verdict = unknown('shared configuration has not synchronized')
    state = effective(verdict, policy, now, previous.effective)
    states = {key: permits(key, state, policy, now) for key, row in consumers().items()
              if row.host == Host.MAC}
    deadlines = [network.observed_at + STATE_TTL]
    if verdict is remote and verdict.metered is not None:
        deadlines.append(remote.evidence_at + HOME_GRACE)
    if policy.mode.until is not None and policy.mode.until > now:
        deadlines.append(policy.mode.until)
    deadlines.extend(value.until for value in policy.approvals.values()
                     if value.until is not None and value.until > now)
    return HostState(Host.MAC, now, verdict, state, states, config.revision,
                     policy.revision, None, min(deadlines))
