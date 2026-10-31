"""Normalize the legacy Pi probe wire format at its trust boundary."""
import ipaddress

from .records import MemberKind, UplinkEvidence, UplinkMember


UPLINK_KINDS = {'wan': MemberKind.UBNT, 'clientwan': MemberKind.STATION,
                'lifiwan': MemberKind.STATION, 'starwan': MemberKind.STATION}


def normalize(raw, home_network):
    unsupported = []
    route = raw.get('local_route', {})
    try:
        local_address = ipaddress.IPv4Address(route.get('prefsrc', ''))
        route_ok = (route.get('gateway') == home_network.gateway and
                    route.get('dev') in ('eth0', 'wlan0') and local_address.is_private)
    except ValueError:
        route_ok = False
    if not route_ok:
        unsupported.append('unrecognized Pi route or VPN route')
    if raw.get('rule_names') != ['default_rule_v4']:
        unsupported.append('source-specific routing rules need review')
    if raw.get('ipv6_defaults') != []:
        unsupported.append('IPv6 routing is unverified or unsupported')
    router = raw.get('router', {})
    if router.get('reachable') is not True:
        unsupported.append('router is unreachable')
    members = []
    for row in router.get('route_members', []):
        if type(row.get('percent')) in (int, float) and row['percent'] == 0:
            continue
        name = row.get('name')
        kind = UPLINK_KINDS.get(name)
        if kind is None:
            unsupported.append('unrecognized selected uplink')
            continue
        radio = raw.get('ubnt', {}) if kind == MemberKind.UBNT else raw.get('wireless_uplinks', {}).get(name, {})
        online = radio.get('up') is True
        if kind == MemberKind.UBNT:
            online = radio.get('reachable') is True and radio.get('connected') is True
        member = {'name': name, 'percent': row.get('percent'),
                  'online': online,
                  'kind': kind.value, 'ssid_verified': radio.get('ssid_verified'),
                  'ssid_configured': radio.get('ssid_configured')}
        members.append(UplinkMember.from_dict(member))
    return UplinkEvidence.from_dict({'collected_at': raw.get('checked_at'),
        'policy': router.get('default_policy'), 'members': [member.as_dict() for member in members],
        'unsupported': unsupported})
