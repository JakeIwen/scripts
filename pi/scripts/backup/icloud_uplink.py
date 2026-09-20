#!/usr/bin/env python3
"""Read-only, fail-closed uplink evidence for the direct iCloud uploader."""
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import connectivity_status


def decide(evidence, blocked_ssids, now=None):
    now = time.time() if now is None else now
    try:
        if not 0 <= now - evidence['checked_at'] <= 30:
            return False, 'stale uplink evidence'
        route = evidence['local_route']
        if route['gateway'] != '192.168.6.1' or route['dev'] not in ('eth0', 'wlan0'):
            return False, 'unrecognized Pi route or VPN route'
        if not ipaddress.ip_address(route['prefsrc']).is_private:
            return False, 'unexpected Pi source address'
        router = evidence['router']
        if not router['reachable'] or router['default_policy'] != 'balanced':
            return False, 'unverified router policy'
        if evidence['rule_names'] != ['default_rule_v4']:
            return False, 'source-specific routing rules need review'
        members = router['route_members']
        if any(type(m.get('percent')) not in (int, float) or not 0 <= m['percent'] <= 100 for m in members):
            return False, 'invalid route weights'
        if not members or abs(sum(m['percent'] for m in members) - 100) > 0.01:
            return False, 'no complete default-route evidence'
        blocked = {s.casefold() for s in blocked_ssids}
        for member in members:
            if member['percent'] <= 0:
                continue
            name = member['name']
            if name not in ('wan', 'clientwan', 'lifiwan'):
                return False, 'unrecognized or Starlink uplink'
            if name == 'wan':
                radio = evidence['ubnt']
                if not radio['reachable'] or not radio['connected']:
                    return False, 'antenna association is unknown'
                ssid = radio['ssid']
            else:
                radio = evidence['wireless_uplinks'].get(name, {})
                if not radio.get('up'):
                    return False, 'wireless uplink identity is unknown'
                ssid = radio.get('ssid')
            if not isinstance(ssid, str) or not ssid.strip():
                return False, 'selected uplink has no verified SSID'
            if ssid.casefold() in blocked or 'starlink' in ssid.casefold():
                return False, 'Starlink is a selected uplink'
        return True, 'all selected uplinks are non-Starlink'
    except (KeyError, TypeError, ValueError, AttributeError):
        return False, 'incomplete uplink evidence'


ROUTER_IDENTITIES = r'''
set -e
/sbin/uci -q show mwan3 | /bin/sed -n 's/^mwan3\.\([^.=]*\)=rule$/RULE \1/p'
for iface in clientwan lifiwan; do
    state=$(/bin/ubus call network.interface.$iface status)
    up=$(printf '%s' "$state" | /usr/bin/jsonfilter -e '@.up')
    printf 'UP %s %s\n' "$iface" "$up"
    if [ "$up" = true ]; then
        dev=$(printf '%s' "$state" | /usr/bin/jsonfilter -e '@.l3_device')
        case "$dev" in ''|*[!a-zA-Z0-9_.:-]*) exit 2;; esac
        ssid=$(/bin/ubus call iwinfo info "{\"device\":\"$dev\"}" | /usr/bin/jsonfilter -e '@.ssid')
        [ -n "$ssid" ]
        printf 'SSID %s %s\n' "$iface" "$ssid"
    fi
done
'''


def collect():
    evidence = connectivity_status.collect_status()
    route = subprocess.run(['/usr/bin/ip', '-j', '-4', 'route', 'get', '1.1.1.1'],
                           check=True, capture_output=True, text=True, timeout=5)
    routes = json.loads(route.stdout)
    if len(routes) != 1:
        raise ValueError('ambiguous local route')
    evidence['local_route'] = routes[0]
    query = subprocess.run(connectivity_status._ssh_args(connectivity_status.ROUTER_TARGET)
                           + [ROUTER_IDENTITIES], check=True, capture_output=True,
                           text=True, timeout=10)
    rules, uplinks = [], {}
    for line in query.stdout.splitlines():
        if line.startswith('RULE '):
            rules.append(line[5:])
        elif line.startswith(('UP ', 'SSID ')):
            kind, name, value = line.split(' ', 2)
            uplinks.setdefault(name, {})['up' if kind == 'UP' else 'ssid'] = (
                value == 'true' if kind == 'UP' else value)
    evidence['rule_names'] = sorted(rules)
    evidence['wireless_uplinks'] = uplinks
    return evidence


if __name__ == '__main__':
    try:
        print(json.dumps(collect()))
    except (OSError, ValueError, subprocess.SubprocessError):
        # Do not expose credentials, SSH output, SSIDs, or HTTP URLs in job logs.
        print(json.dumps({'error': 'uplink inspection failed', 'checked_at': time.time()}))
        sys.exit(1)
