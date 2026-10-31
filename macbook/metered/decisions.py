"""Pure Mac decision/lease calculation; Pi telemetry is never a local approval."""
from shared.metered.records import Host, HostState, MacNetwork
from shared.metered.registry import consumers
from shared.metered.rules import EVIDENCE_TTL, effective, mac_verdict, permits, unknown
from .config import STATE_TTL


def leased(state, now):
    return (state is not None and 0 <= now - state.evaluated_at <= STATE_TTL and
            state.valid_until is not None and now < state.valid_until)


def home_verdict(pi, config, authoritative, now):
    if (pi.host != Host.PI or not leased(pi, now) or
            pi.config_revision != config.revision or
            pi.verdict.evidence_at is None or
            not 0 <= now - pi.verdict.evidence_at <= EVIDENCE_TTL):
        return unknown('Pi uplink state is missing or stale')
    if set(config.metered_ssids) - set(authoritative.metered_ssids):
        return unknown('Pi has not evaluated pending metered SSIDs')
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
        deadlines.extend([pi.valid_until, pi.verdict.evidence_at + EVIDENCE_TTL])
    if policy.mode.until is not None and policy.mode.until > now:
        deadlines.append(policy.mode.until)
    deadlines.extend(value.until for value in policy.approvals.values()
                     if value.until is not None and value.until > now)
    return HostState(Host.MAC, now, verdict, state, states, config.revision,
                     policy.revision, None, min(deadlines))
