#!/usr/bin/env python3
"""Read-only, fail-closed uplink evidence for the direct iCloud uploader."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import connectivity_status


def decide(evidence, consumer_id='pi.icloud_backup', command=subprocess.run):
    # Keep long-lived flat cloud workers outside immutable package releases.
    # Only the short-lived admission CLI imports the shared metering policy.
    try:
        result = command(['/home/pi/scripts/vanpi-metered', 'admit', consumer_id],
                         input=json.dumps(evidence), capture_output=True, text=True, timeout=5)
        payload = json.loads(result.stdout)
        if (result.returncode or not isinstance(payload, dict)
                or type(payload.get('allowed')) is not bool or not isinstance(payload.get('reason'), str)):
            raise ValueError('invalid admission response')
        return payload['allowed'], payload['reason']
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return False, 'metering admission is unavailable'


ROUTER_IDENTITIES = r'''
set -e
/sbin/uci -q show mwan3 | /bin/sed -n 's/^mwan3\.\([^.=]*\)=rule$/RULE \1/p'
for iface in clientwan lifiwan starwan; do
    state=$(/bin/ubus call network.interface.$iface status 2>/dev/null) || {
        printf 'UP %s false\n' "$iface"; continue;
    }
    up=$(printf '%s' "$state" | /usr/bin/jsonfilter -e '@.up') || up=false
    printf 'UP %s %s\n' "$iface" "$up"
    if [ "$up" = true ]; then
        dev=$(printf '%s' "$state" | /usr/bin/jsonfilter -e '@.l3_device') || continue
        case "$dev" in ''|*[!a-zA-Z0-9_.:-]*) continue;; esac
        info=$(/bin/ubus -S call iwinfo info "{\"device\":\"$dev\"}") || continue
        printf 'RADIO %s %s\n' "$iface" "$info"
    fi
done
'''

ROUTER_ROUTES = ("printf '__VAN_DASH_DEFAULT_POLICY__='; "
                 "/sbin/uci -q get mwan3.default_rule_v4.use_policy; "
                 "/usr/sbin/mwan3 policies")


def probe_ssh_args(target, identity=None):
    # Reuse the authenticated LAN connection, never the probe result. Slow
    # antenna key exchange otherwise suspends cloud traffic on every check.
    directory = Path.home() / '.vanpi-icloud-uplink'
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('unsafe uplink connection directory')
    args = connectivity_status._ssh_args(target, identity)
    return args[:-1] + ['-o', 'ControlMaster=auto', '-o', 'ControlPersist=30s',
                       '-o', 'ControlPath=' + str(directory / '%C'), args[-1]]


def collect(command=connectivity_status.run_command, wall_clock=time.time):
    # Backup admission needs fresh routing/association evidence, not dashboard
    # radio statistics or a reachability ping to an unused antenna.
    evidence = {'checked_at': wall_clock(), 'ubnt': {}}
    route = command(['/usr/bin/ip', '-j', '-4', 'route', 'get', '1.1.1.1'], timeout=5)
    route.check_returncode()
    routes = json.loads(route.stdout)
    if len(routes) != 1:
        raise ValueError('ambiguous local route')
    evidence['local_route'] = routes[0]
    ipv6 = command(['/usr/bin/ip', '-j', '-6', 'route', 'show', 'default'], timeout=5)
    ipv6.check_returncode()
    evidence['ipv6_defaults'] = json.loads(ipv6.stdout)
    query = command(probe_ssh_args(connectivity_status.ROUTER_TARGET)
                    + [ROUTER_ROUTES + '\n' + ROUTER_IDENTITIES], timeout=10)
    query.check_returncode()
    policy, members = connectivity_status.parse_mwan3_default_route(query.stdout)
    evidence['router'] = {'reachable': True, 'default_policy': policy, 'route_members': members}
    rules, uplinks = [], {}
    for line in query.stdout.splitlines():
        if line.startswith('RULE '):
            rules.append(line[5:])
        elif line.startswith(('UP ', 'RADIO ')):
            kind, name, value = line.split(' ', 2)
            uplink = uplinks.setdefault(name, {})
            if kind == 'UP':
                uplink['up'] = value == 'true'
            else:
                radio = json.loads(value)
                uplink.update({key: radio.get(key) for key in ('ssid', 'bssid', 'mode')})
    for uplink in uplinks.values():
        ssid = uplink.get('ssid')
        bssid = uplink.get('bssid', '')
        associated = (uplink.get('up') and uplink.get('mode') == 'Client'
                      and isinstance(bssid, str) and isinstance(ssid, str)
                      and connectivity_status.MAC_ADDRESS.fullmatch(bssid)
                      and bssid.lower() not in ('00:00:00:00:00:00', 'ff:ff:ff:ff:ff:ff'))
        uplink.update(ssid_configured=None, ssid_verified=ssid if associated else None,
                      starlink=connectivity_status.is_starlink_ssid(ssid))
    evidence['rule_names'] = sorted(rules)
    evidence['wireless_uplinks'] = uplinks
    if any(m['name'] == 'wan' and m['percent'] > 0 for m in members):
        association, radio = connectivity_status.collect_ubnt_association(
            command, probe_ssh_args(connectivity_status.UBNT_TARGET,
                                    connectivity_status.UBNT_IDENTITY),
            connectivity_status.UBNT_STATUS_COMMAND)
        association.check_returncode()
        evidence['ubnt'] = {'reachable': True, **radio,
                            **connectivity_status.verified_ubnt_identity(association.stdout),
                            'starlink': connectivity_status.is_starlink_ssid(radio['ssid'])}
    return evidence


if __name__ == '__main__':
    try:
        print(json.dumps(collect()))
    except (OSError, ValueError, subprocess.SubprocessError):
        # Do not expose credentials, SSH output, SSIDs, or HTTP URLs in job logs.
        print(json.dumps({'error': 'uplink inspection failed', 'checked_at': time.time()}))
        sys.exit(1)
