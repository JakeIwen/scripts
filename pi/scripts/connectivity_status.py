#!/usr/bin/env python3
"""Report van network uplink, UBNT radio, or OpenWrt client state as JSON.

This collector is intentionally passive and cheap: it reuses mwan3's existing
reachability tracking, pings the UBNT once to distinguish an unplugged cable,
and reads the UBNT's current association data without running a wireless scan.
The optional ``--clients`` mode performs one bounded, read-only router query for
the dashboard's OpenWrt sheet; it is not part of the recurring uplink poll.
"""

import ipaddress
import json
import os
import re
import shlex
import subprocess
import sys
import time


ROUTER_TARGET = os.environ.get("CONNECTIVITY_ROUTER_TARGET", "root@OpenWrt")
UBNT_TARGET = os.environ.get("CONNECTIVITY_UBNT_TARGET", "ubnt@192.168.8.20")
UBNT_HOST = os.environ.get("CONNECTIVITY_UBNT_HOST", UBNT_TARGET.rsplit("@", 1)[-1])
UBNT_IDENTITY = os.path.expanduser(
    os.environ.get("CONNECTIVITY_UBNT_IDENTITY", "~/.ssh/id_rsa")
)
SSH = os.environ.get("CONNECTIVITY_SSH", "/usr/bin/ssh")
PING = os.environ.get("CONNECTIVITY_PING", "/usr/bin/ping")

MWAN_PRIORITY = ("clientwan", "lifiwan", "wan")
ROUTER_CLIENTS_COMMAND = r"""
printf '__VAN_DASH_LEASES__\n'
/bin/cat /tmp/dhcp.leases 2>/dev/null || true
printf '__VAN_DASH_NEIGHBORS__\n'
/sbin/ip neigh show dev br-lan 2>/dev/null || true
printf '__VAN_DASH_STATIC_HOSTS__\n'
/sbin/uci -q show dhcp 2>/dev/null \
  | /bin/grep -E "^dhcp\.@host\[[0-9]+\]\.(name|ip|mac)=" || true
printf '__VAN_DASH_HOSTAPD__\n'
for object in $(/bin/ubus -S list 'hostapd.*' 2>/dev/null); do
  printf 'OBJECT %s\n' "$object"
  /bin/ubus -S call "$object" get_clients 2>/dev/null || true
done
printf '__VAN_DASH_RADIOS__\n'
wireless_status=$(/bin/ubus -S call network.wireless status 2>/dev/null || true)
for radio in radio0 radio1; do
  band=$(/sbin/uci -q get "wireless.$radio.band" 2>/dev/null || true)
  [ -n "$band" ] || continue
  printf '%s\n' "$wireless_status" \
    | /usr/bin/jsonfilter -e "@.$radio.interfaces[*].ifname" 2>/dev/null \
    | while IFS= read -r ifname; do
        [ -n "$ifname" ] && printf '%s|%s|%s\n' "$ifname" "$radio" "$band"
      done
done
true
""".strip()
CLIENT_SECTION = re.compile(r"^__VAN_DASH_([A-Z_]+)__$")
MAC_ADDRESS = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$", re.IGNORECASE)
WIRELESS_INTERFACE = re.compile(r"^[a-zA-Z0-9_.:-]{1,50}$")
RADIO_NAME = re.compile(r"^radio\d+$")
RADIO_BANDS = {"2g": "2.4 GHz", "5g": "5 GHz", "6g": "6 GHz"}
ACTIVE_NEIGHBOR_STATES = {"REACHABLE", "DELAY", "PROBE", "PERMANENT"}


def run_command(args, timeout=8):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def parse_mwan3_interfaces(output):
    """Parse ``mwan3 interfaces`` without depending on OpenWrt JSON helpers."""
    interfaces = []
    pattern = re.compile(
        r"^\s*interface\s+(?P<name>\S+)\s+is\s+"
        r"(?P<state>online|offline|disabled|unknown)(?P<detail>.*)$",
        re.IGNORECASE,
    )
    for line in output.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        detail = match.group("detail").strip(" ,")
        interfaces.append(
            {
                "name": match.group("name"),
                "state": match.group("state").lower(),
                "tracking": "paused" if "tracking is paused" in detail.lower() else "active",
                "detail": detail or None,
            }
        )
    return interfaces


def select_mode(interfaces):
    online = {item["name"] for item in interfaces if item["state"] == "online"}
    ordered = []
    for name in MWAN_PRIORITY:
        if name in online:
            ordered.append(name)
    ordered.extend(
        item["name"]
        for item in interfaces
        if item["state"] == "online" and item["name"] not in ordered
    )
    return " + ".join(ordered) or None


def _valid_ipv4(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    return str(address) if address.version == 4 else None


def _valid_mac(value):
    value = str(value or "").lower()
    return value if MAC_ADDRESS.fullmatch(value) else None


def split_client_sections(output):
    sections = {}
    current = None
    for line in output.splitlines():
        marker = CLIENT_SECTION.fullmatch(line.strip())
        if marker:
            current = marker.group(1).lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines) for name, lines in sections.items()}


def parse_dhcp_leases(output):
    leases = {}
    for line in output.splitlines():
        fields = line.split(maxsplit=4)
        if len(fields) < 4:
            continue
        expires, mac_value, ip_value, hostname = fields[:4]
        mac = _valid_mac(mac_value)
        ip = _valid_ipv4(ip_value)
        try:
            expires_at = int(expires)
        except ValueError:
            expires_at = None
        if not mac or not ip:
            continue
        leases[mac] = {
            "ip": ip,
            "hostname": None if hostname in ("", "*") else hostname[:100],
            "lease_expires_at": expires_at,
        }
    return leases


def parse_static_hosts(output):
    sections = {}
    pattern = re.compile(
        r"^dhcp\.@host\[(?P<index>\d+)\]\.(?P<field>name|ip|mac)=(?P<value>.*)$"
    )
    for line in output.splitlines():
        match = pattern.fullmatch(line.strip())
        if not match:
            continue
        try:
            values = shlex.split(match.group("value"))
        except ValueError:
            continue
        if len(values) != 1:
            continue
        sections.setdefault(match.group("index"), {})[match.group("field")] = values[0]

    by_mac = {}
    for item in sections.values():
        mac = _valid_mac(item.get("mac"))
        ip = _valid_ipv4(item.get("ip"))
        if not mac:
            continue
        name = item.get("name")
        by_mac[mac] = {
            "ip": ip,
            "hostname": name[:100] if name else None,
        }
    return by_mac


def parse_neighbors(output):
    neighbors = {}
    known_states = ACTIVE_NEIGHBOR_STATES | {"STALE", "FAILED", "INCOMPLETE", "NOARP"}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        ip = _valid_ipv4(fields[0])
        try:
            mac_index = fields.index("lladdr") + 1
        except ValueError:
            continue
        if mac_index >= len(fields):
            continue
        mac = _valid_mac(fields[mac_index])
        state = next((field for field in reversed(fields) if field in known_states), None)
        if not ip or not mac or not state:
            continue
        current = neighbors.get(mac)
        if current is None or (
            state in ACTIVE_NEIGHBOR_STATES
            and current["state"] not in ACTIVE_NEIGHBOR_STATES
        ):
            neighbors[mac] = {"ip": ip, "state": state}
    return neighbors


def parse_hostapd_clients(output):
    clients = {}
    current_object = None
    payload_lines = []

    def consume():
        if current_object is None or not payload_lines:
            return
        try:
            payload = json.loads("\n".join(payload_lines))
        except (TypeError, ValueError):
            return
        values = payload.get("clients", {}) if isinstance(payload, dict) else {}
        if not isinstance(values, dict):
            return
        for mac_value, details in values.items():
            mac = _valid_mac(mac_value)
            if (
                not mac
                or not isinstance(details, dict)
                or details.get("assoc") is not True
                or details.get("authorized") is not True
            ):
                continue
            signal = details.get("signal")
            rates = details.get("rate", {})
            byte_counts = details.get("bytes", {})
            clients[mac] = {
                "interface": current_object.removeprefix("hostapd."),
                "signal_dbm": signal if isinstance(signal, int) else None,
                "rx_rate_bps": rates.get("rx") if isinstance(rates, dict) else None,
                "tx_rate_bps": rates.get("tx") if isinstance(rates, dict) else None,
                "rx_bytes": byte_counts.get("rx") if isinstance(byte_counts, dict) else None,
                "tx_bytes": byte_counts.get("tx") if isinstance(byte_counts, dict) else None,
            }

    for line in output.splitlines():
        if line.startswith("OBJECT "):
            consume()
            current_object = line[7:].strip()
            payload_lines = []
        elif current_object is not None:
            payload_lines.append(line)
    consume()
    return clients


def parse_radio_interfaces(output):
    interfaces = {}
    for line in output.splitlines():
        fields = line.strip().split("|")
        if len(fields) != 3:
            continue
        interface, radio, raw_band = fields
        band = RADIO_BANDS.get(raw_band)
        if (
            not WIRELESS_INTERFACE.fullmatch(interface)
            or not RADIO_NAME.fullmatch(radio)
            or band is None
        ):
            continue
        interfaces[interface] = {"radio": radio, "band": band}
    return interfaces


def parse_router_clients(output, checked_at):
    sections = split_client_sections(output)
    leases = parse_dhcp_leases(sections.get("leases", ""))
    static_hosts = parse_static_hosts(sections.get("static_hosts", ""))
    neighbors = parse_neighbors(sections.get("neighbors", ""))
    wireless = parse_hostapd_clients(sections.get("hostapd", ""))
    radio_interfaces = parse_radio_interfaces(sections.get("radios", ""))
    connected_macs = set(wireless)
    connected_macs.update(
        mac
        for mac, neighbor in neighbors.items()
        if neighbor["state"] in ACTIVE_NEIGHBOR_STATES
    )

    clients = []
    for mac in connected_macs:
        lease = leases.get(mac, {})
        static = static_hosts.get(mac, {})
        neighbor = neighbors.get(mac, {})
        radio = wireless.get(mac)
        radio_interface = (
            radio_interfaces.get(radio.get("interface"), {}) if radio else {}
        )
        hostname = lease.get("hostname") or static.get("hostname")
        clients.append(
            {
                "name": hostname or "Unknown device",
                "hostname_known": bool(hostname),
                "ip": lease.get("ip") or neighbor.get("ip") or static.get("ip"),
                "mac": mac,
                "connection": "wifi" if radio is not None else "lan",
                "interface": radio.get("interface") if radio else "br-lan",
                "radio": radio_interface.get("radio"),
                "band": radio_interface.get("band"),
                "neighbor_state": neighbor.get("state"),
                "signal_dbm": radio.get("signal_dbm") if radio else None,
                "rx_rate_bps": radio.get("rx_rate_bps") if radio else None,
                "tx_rate_bps": radio.get("tx_rate_bps") if radio else None,
                "rx_bytes": radio.get("rx_bytes") if radio else None,
                "tx_bytes": radio.get("tx_bytes") if radio else None,
                "lease_expires_at": lease.get("lease_expires_at"),
            }
        )
    clients.sort(
        key=lambda item: (
            not item["hostname_known"],
            item["name"].casefold(),
            tuple(int(part) for part in item["ip"].split(".")) if item["ip"] else (999,),
            item["mac"],
        )
    )
    return {
        "version": 1,
        "checked_at": checked_at,
        "client_count": len(clients),
        "wifi_count": sum(item["connection"] == "wifi" for item in clients),
        "lan_count": sum(item["connection"] == "lan" for item in clients),
        "clients": clients,
    }


def collect_clients(command=run_command, wall_clock=time.time):
    result = command(
        _ssh_args(ROUTER_TARGET) + [ROUTER_CLIENTS_COMMAND],
        timeout=12,
    )
    if result.returncode != 0:
        raise RuntimeError(_failure_detail(result))
    return parse_router_clients(result.stdout, int(wall_clock()))


def _number(pattern, output, cast=int):
    match = re.search(pattern, output, re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    try:
        return cast(match.group(1))
    except (TypeError, ValueError):
        return None


def parse_ubnt_wireless(output):
    """Separate the configured ESSID from actual AP association state."""
    # AirOS can expose a profile label (for example STARLINK) as ESSID while
    # wpa_supplicant targets the real access-point SSID (for example denlink).
    essid_match = re.search(r"^target_ssid=(.*)$", output, re.IGNORECASE | re.MULTILINE)
    if not essid_match:
        essid_match = re.search(r'ESSID:"([^"]*)"', output, re.IGNORECASE)
    if not essid_match:
        essid_match = re.search(r"^essid=(.*)$", output, re.IGNORECASE | re.MULTILINE)
    ssid = essid_match.group(1).strip() if essid_match else None
    if ssid in ("", "off/any"):
        ssid = None

    ap_match = re.search(r"Access Point:\s*(\S+)", output, re.IGNORECASE)
    access_point = ap_match.group(1).strip() if ap_match else None
    disconnected_aps = {None, "not-associated", "00:00:00:00:00:00"}
    connected = access_point.lower() not in disconnected_aps if access_point else False

    quality_match = re.search(r"Link Quality[=:]\s*(\d+)\s*/\s*(\d+)", output, re.IGNORECASE)
    quality_percent = None
    if quality_match and int(quality_match.group(2)):
        quality_percent = round(100 * int(quality_match.group(1)) / int(quality_match.group(2)))

    bitrate_match = re.search(
        r"Bit Rate[=:]\s*([0-9.]+)\s*([^\s]+(?:/s)?)", output, re.IGNORECASE
    )
    bitrate = None
    if bitrate_match:
        bitrate = f"{bitrate_match.group(1)} {bitrate_match.group(2)}"

    # AirOS mca-status reports CCQ in tenths of a percent (991 means 99.1%).
    ccq_raw = _number(r"^ccq=(-?\d+)", output)
    ccq_percent = None
    if ccq_raw is not None and 0 <= ccq_raw <= 1000:
        ccq_percent = round(ccq_raw / 10, 1)

    return {
        "connected": connected,
        "ssid": ssid,
        "access_point": access_point,
        "signal_dbm": _number(r"Signal level[=:]\s*(-?\d+)", output),
        "noise_dbm": _number(r"Noise level[=:]\s*(-?\d+)", output),
        "quality_percent": quality_percent,
        "ccq_percent": ccq_percent,
        "bitrate": bitrate,
        "frequency_ghz": _number(r"Frequency[=:]\s*([0-9.]+)\s*GHz", output, float),
    }


def _ssh_args(target, identity=None):
    args = [
        SSH,
        "-n",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=4",
    ]
    if identity:
        args.extend(("-i", identity))
    args.append(target)
    return args


def _failure_detail(result):
    detail = (result.stderr or result.stdout or "command failed").strip()
    return detail[-300:] if detail else "command failed"


def collect_status(command=run_command, wall_clock=time.time):
    checked_at = int(wall_clock())
    router = {
        "reachable": False,
        "mode": None,
        "online": [],
        "interfaces": [],
        "error": None,
    }
    internet_online = None

    try:
        result = command(
            _ssh_args(ROUTER_TARGET) + ["/usr/sbin/mwan3 interfaces"], timeout=8
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        router["error"] = str(exc)
    else:
        if result.returncode == 0:
            router["reachable"] = True
            router["interfaces"] = parse_mwan3_interfaces(result.stdout)
            router["online"] = [
                item["name"] for item in router["interfaces"] if item["state"] == "online"
            ]
            router["mode"] = select_mode(router["interfaces"])
            if router["interfaces"]:
                internet_online = bool(router["online"])
            else:
                router["error"] = "mwan3 returned no interface state"
        else:
            router["error"] = _failure_detail(result)

    ubnt = {
        "reachable": False,
        "connected": False,
        "ssid": None,
        "access_point": None,
        "signal_dbm": None,
        "noise_dbm": None,
        "quality_percent": None,
        "ccq_percent": None,
        "bitrate": None,
        "frequency_ghz": None,
        "error": None,
    }
    try:
        ping = command([PING, "-c", "1", "-W", "2", UBNT_HOST], timeout=4)
    except (OSError, subprocess.TimeoutExpired) as exc:
        ubnt["error"] = str(exc)
    else:
        ubnt["reachable"] = ping.returncode == 0
        if not ubnt["reachable"]:
            ubnt["error"] = "UBNT did not answer ping (check Ethernet cable/power)"

    if ubnt["reachable"]:
        remote_status = (
            "/sbin/iwconfig ath0 2>&1; "
            "/usr/bin/mca-status 2>/dev/null | "
            "/bin/grep -E '^(essid|signal|noise|ccq|uptime)=' || true; "
            "/usr/bin/awk -F= '/^wpasupplicant\\.profile\\.1\\.network\\.1\\.ssid=/"
            "{print \"target_ssid=\" $2; exit}' /tmp/system.cfg"
        )
        try:
            result = command(
                _ssh_args(UBNT_TARGET, UBNT_IDENTITY) + [remote_status], timeout=8
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            ubnt["error"] = f"UBNT status failed: {exc}"
        else:
            if result.returncode == 0:
                ubnt.update(parse_ubnt_wireless(result.stdout))
                ubnt["error"] = None
            else:
                ubnt["error"] = f"UBNT status failed: {_failure_detail(result)}"

    return {
        "checked_at": checked_at,
        "internet": {
            "online": internet_online,
            "source": "mwan3 reachability tracking",
        },
        "router": router,
        "ubnt": ubnt,
    }


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if args == []:
        collector = collect_status
    elif args == ["--clients"]:
        collector = collect_clients
    else:
        print("usage: connectivity_status.py [--clients]", file=sys.stderr)
        return 2
    try:
        payload = collector()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
