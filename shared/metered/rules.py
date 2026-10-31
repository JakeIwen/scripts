"""Pure metering decisions; callers supply clocks and network evidence."""
from .records import (
    ApprovalKind, ConsumerState, Effective, EffectiveSource, MatchedSSID,
    MemberKind, ModeValue, Verdict,
)


EVIDENCE_TTL = 30.0


def unknown(reason, evidence_at=None):
    return Verdict(None, reason, [], evidence_at)


def dendelion_verdict(evidence, config, now):
    if not 0 <= now - evidence.collected_at <= EVIDENCE_TTL:
        return unknown('stale uplink evidence', evidence.collected_at)
    if evidence.unsupported:
        return unknown('; '.join(evidence.unsupported), evidence.collected_at)
    members = evidence.members
    if (not evidence.policy or not members or
            abs(sum(member.percent for member in members) - 100) > 0.01 or
            len({member.name for member in members}) != len(members)):
        return unknown('incomplete default-policy evidence', evidence.collected_at)
    matched = []
    for member in members:
        if member.percent <= 0:
            continue
        if not member.online:
            return unknown('selected uplink is not online', evidence.collected_at)
        if member.kind != MemberKind.WIRED and member.ssid_verified is None:
            return unknown('selected uplink identity is unknown', evidence.collected_at)
        if member.ssid_verified in config.metered_ssids:
            matched.append(MatchedSSID(member.name, member.ssid_verified))
    return Verdict(bool(matched), 'metered SSID selected' if matched else
                   'all selected uplinks are unmetered', matched, evidence.collected_at)


def mac_verdict(mac_net, dendelion, config):
    if not mac_net.connected:
        return unknown('Mac network is disconnected')
    # A direct configured match is sufficient even when home cannot be identified.
    if mac_net.ssid in config.metered_ssids:
        return Verdict(True, 'Mac joined a metered SSID',
                       [MatchedSSID('mac', mac_net.ssid)], None)
    home = config.home_network
    on_home = mac_net.ssid == home.ssid if mac_net.ssid is not None else (
        mac_net.gateway == home.gateway and mac_net.gateway_mac == home.gateway_mac)
    if on_home:
        return dendelion
    if mac_net.ssid is None:
        return unknown('Mac SSID is unreadable away from the verified home network')
    return Verdict(False, 'Mac SSID is not metered', [], None)


def effective(verdict, policy, now, previous=None):
    mode = policy.mode
    active = mode.set_at <= now and (mode.until is None or now < mode.until)
    source = EffectiveSource.VERDICT
    metered = verdict.metered is not False
    if mode.value != ModeValue.AUTO and active:
        metered = mode.value == ModeValue.METERED
        source = EffectiveSource.MODE
    episode = None
    if metered:
        # The host serializes transitions; timestamp + revision keeps this pure.
        episode = (previous.episode if previous and previous.metered else None)
        episode = episode or f'{now:.9f}:{policy.revision}'
    return Effective(metered, source, episode)


def approval_active(approval, state, now):
    if approval is None or approval.set_at > now:
        return False
    checks = {
        ApprovalKind.UNTIL: lambda: now < approval.until,
        ApprovalKind.EPISODE: lambda: state.metered and approval.episode == state.episode,
    }
    return checks[approval.kind]()


def permits(consumer_id, state, policy, now):
    """Evaluate a registry-validated consumer id without reading the registry."""
    approval = policy.approvals.get(consumer_id)
    active = approval_active(approval, state, now)
    reason = 'held on a metered or unknown network'
    allowed = False
    if consumer_id in policy.exempt:
        allowed, reason = True, 'not gated (explicit exemption)'
    elif not state.metered:
        allowed, reason = True, 'unmetered'
    elif active:
        allowed, reason = True, 'temporary approval'
    return ConsumerState(allowed, reason, approval if active else None)
